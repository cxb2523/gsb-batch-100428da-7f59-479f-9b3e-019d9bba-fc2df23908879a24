#!/usr/bin/env python3
"""
Deep, continuous TikTok crawler (pure API - no browser):

Idea (very deep search):
  - Start from search keywords/hashtags + optional seed accounts.
  - Each keyword -> discover accounts via /api/search/user/full/.
  - Each account -> fetch its followers (scene=67) and scan every follower bio
    for a phone number.
  - Each (non-private) follower becomes a new node whose followers are also
    expanded... and so on (BFS over the follower graph) up to a large depth, so
    the search keeps going and deepens automatically.
  - Incremental save to output/tiktok-bio.json (name + number + country), de-duplicated.
  - Stop anytime with Ctrl+C; progress is saved.

Persistent state:
  - A state file (crawl_state.json) records every user that was already crawled.
    On startup it is loaded and those users are skipped, so they are never
    re-crawled across runs. Only accounts whose follower pagination COMPLETED
    are written here; partially-failed accounts are retried on the next run.
  - An outcomes file (crawl_outcomes.jsonl) gets one JSON record per processed
    account: account, depth, followers fetched, pages, complete?, status
    (success/partial/failed), failure reason (empty_response / status_code /
    cursor_anomaly / interrupted / exception) and attempt count. A run summary
    (success/partial/failed counts) is logged at the end and on Ctrl+C.

Resilient pagination:
  - Empty responses, non-zero status_code and cursor anomalies (repeated or
    regressing cursor) are retried per account with bounded exponential backoff
    (--page-retries / --retry-backoff). If retries are exhausted, only that
    account's pagination stops; followers already fetched are kept, their bios
    are still scanned and saved, and the run continues with the next account.

Usage:
    python3 deep_search.py --keywords "مطعم" "عقارات" "متجر"
    python3 deep_search.py --seeds coderoot.ksa --max-depth 5 --max-followers 800
    python3 deep_search.py --keywords "توصيل" --max-users 100000 --max-depth 6
"""

import os
import time
import json
import random
import argparse
import urllib.parse as up
from collections import deque

import search_bio as sb


def now():
    return time.strftime("%H:%M:%S")


class DeepCrawler:
    def __init__(self, cfg, out_path, max_depth, max_followers, max_users,
                 delay_range, state_path, outcomes_path=None,
                 page_retries=3, retry_backoff=1.0):
        self.api = sb.TikTokAPI(cfg)
        self.out_path = out_path
        self.state_path = state_path
        self.outcomes_path = outcomes_path
        self.max_depth = max_depth
        self.max_followers = max_followers
        self.max_users = max_users
        self.delay_range = delay_range
        self.page_retries = max(0, page_retries)      # bounded retries per anomaly
        self.retry_backoff = max(0.0, retry_backoff)  # base seconds, exponential

        self.frontier = deque()      # items: (secUid, uniqueId, depth)
        self.visited = set()         # uniqueIds fully crawled (complete pagination)
        self.enqueued = set()        # uniqueIds queued or processed
        self.checked_bio = set()     # uniqueIds whose bio was already scanned
        self.users_processed = 0
        self.phones_found = 0
        self._dirty_since_save = 0

        # per-account outcome records + run summary
        self.outcomes = []
        self.summary = {"success": 0, "partial": 0, "failed": 0}

        self._load_state()

    # ---------- persistent state (avoid re-crawling previously pulled users) ----------
    def _load_state(self):
        """Load the list of users crawled in previous runs so we skip them."""
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            done = data.get("crawled") if isinstance(data, dict) else data
            done = set(done or [])
            # Treat previously-crawled users as done and never enqueue them again.
            self.visited |= done
            self.enqueued |= done
            sb.log(f"[{now()}] [state] loaded {len(done)} previously-crawled users (will be skipped).")
        except Exception as e:
            sb.log(f"[{now()}] [warn] could not read state file: {e}")

    def _save_state(self):
        if not self.state_path:
            return
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({"crawled": sorted(self.visited)}, f, ensure_ascii=False)
            self._dirty_since_save = 0
        except Exception as e:
            sb.log(f"[{now()}] [warn] could not save state file: {e}")

    # ---------- helpers ----------
    def _sleep(self):
        lo, hi = self.delay_range
        if hi > 0:
            time.sleep(random.uniform(lo, hi))

    def _retry_sleep(self, retry_no):
        """Exponential backoff with jitter before retrying a failed page."""
        if self.retry_backoff <= 0:
            return
        wait = min(self.retry_backoff * (2 ** max(0, retry_no - 1)), 30.0)
        time.sleep(wait + random.uniform(0, wait * 0.25))

    # ---------- per-account outcome records ----------
    def _record_outcome(self, outcome):
        """Store one outcome record per processed account and append it to the
        outcomes JSONL file (so Ctrl+C never loses the records)."""
        self.outcomes.append(outcome)
        self.summary[outcome["status"]] += 1
        sb.log(f"[{now()}] [outcome] @{outcome['account']} depth={outcome['depth']} "
               f"status={outcome['status']} followers={outcome['followers']} "
               f"complete={outcome['complete']} reason={outcome['fail_reason']} "
               f"attempts={outcome['attempts']}")
        if not self.outcomes_path:
            return
        try:
            os.makedirs(os.path.dirname(self.outcomes_path), exist_ok=True)
            with open(self.outcomes_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        except Exception as e:
            sb.log(f"[{now()}] [warn] could not write outcome record: {e}")

    def log_summary(self):
        s = self.summary
        total = s["success"] + s["partial"] + s["failed"]
        sb.log(f"[{now()}] [summary] accounts={total} | success={s['success']} "
               f"| partial={s['partial']} | failed={s['failed']}")

    def _save_bio(self, user, source):
        """Scan a user's bio and save any numbers (once per user)."""
        uid = user.get("uniqueId")
        if not uid or uid in self.checked_bio:
            return 0
        self.checked_bio.add(uid)
        recs = sb.build_records([user], source=source)
        if recs:
            added, total = sb.save_results(recs, self.out_path)
            self.phones_found += added
            if added:
                nums = [r["phone"] for r in recs]
                sb.log(f"    [phone] {uid}: {nums}  (total numbers {total})")
            return added
        return 0

    def _maybe_enqueue(self, secUid, uid, depth, private):
        if not secUid or not uid or uid in self.enqueued:
            return
        if private:
            return
        if depth > self.max_depth:
            return
        self.enqueued.add(uid)
        self.frontier.append((secUid, uid, depth))

    # ---------- discovery via search keywords ----------
    def seed_from_keyword(self, keyword, depth=0):
        kw = keyword.lstrip("#").strip()
        sb.log(f"[{now()}] [search] '{kw}'")
        offset = 0
        found = 0
        for _ in range(6):   # several pages per keyword
            d = self.api._get(
                "/api/search/user/full/",
                {"from_page": "search", "keyword": kw, "offset": str(offset),
                 "cursor": str(offset), "search_id": "", "web_search_code": "{}"},
                f"https://www.tiktok.com/search/user?q={up.quote(kw)}",
                use_dynosaur=True)
            if not d:
                break
            users = d.get("user_list") or []
            for x in users:
                ui = x.get("user_info", {})
                uid = ui.get("unique_id")
                sec = ui.get("sec_uid")
                user = {"uniqueId": uid, "nickname": ui.get("nickname") or "",
                        "signature": ui.get("signature") or ""}
                self._save_bio(user, source=f"search:{kw}")
                self._maybe_enqueue(sec, uid, depth + 1,
                                    private=ui.get("private_account"))
                found += 1
            if not d.get("has_more"):
                break
            offset = d.get("cursor") or (offset + 10)
            self._sleep()
        sb.log(f"[{now()}] [search] '{kw}': queued {found} candidate(s).")

    def seed_from_username(self, username, depth=0):
        info = self.api.resolve_user(username)
        if not info:
            sb.log(f"  [warn] could not resolve @{username}")
            return
        self._save_bio(info, source=f"seed:@{username}")
        self._maybe_enqueue(info["secUid"], info["uniqueId"], depth, private=False)

    # ---------- follower expansion ----------
    def crawl_followers(self, secUid, uid, depth):
        """Fetch an account's followers, scan their bios, and queue them to go deeper.

        Resilient to partial failures: empty responses, non-zero status_code and
        cursor anomalies (repeated/regressing cursor) are retried per account with
        bounded exponential backoff. If retries are exhausted, pagination stops for
        THIS account only; followers already collected are kept, their bios are
        still scanned and saved, and an outcome record is written. Returns the
        outcome dict. On Ctrl+C the partial outcome is recorded before re-raising.
        """
        cursor = "0"
        seen_cursors = {"0"}
        seen_followers = set()   # dedup: retried/overlapping pages must not double-count
        collected = 0
        page = 0
        attempts = 0
        retries_used = 0
        fail_reason = None
        complete = False
        try:
            while collected < self.max_followers:
                attempts += 1
                d = self.api._get(
                    "/api/user/list/",
                    {"from_page": "user", "secUid": secUid, "count": "30",
                     "minCursor": cursor, "maxCursor": "0", "scene": sb.FOLLOWERS_SCENE},
                    "https://www.tiktok.com/")
                anomaly = None
                if not d:
                    anomaly = "empty_response"
                elif d.get("status_code") not in (0, None):
                    anomaly = "status_code"
                if anomaly:
                    if retries_used < self.page_retries:
                        retries_used += 1
                        sb.log(f"  [retry {retries_used}/{self.page_retries}] @{uid}: "
                               f"{anomaly} on page {page + 1} - backing off.")
                        self._retry_sleep(retries_used)
                        continue
                    fail_reason = anomaly
                    if anomaly == "empty_response":
                        sb.log("  [warn] empty responses persist - session/token may have "
                               "expired. Refresh cookie and x_dynosaur in config.json.")
                    break
                page += 1
                ulist = d.get("userList") or []
                for x in ulist:
                    u = x.get("user", {})
                    fuid = u.get("uniqueId")
                    if fuid and fuid in seen_followers:
                        continue
                    if fuid:
                        seen_followers.add(fuid)
                    user = {"uniqueId": fuid,
                            "nickname": u.get("nickname") or "",
                            "signature": u.get("signature") or ""}
                    self._save_bio(user, source=f"follower_of:@{uid}")
                    self._maybe_enqueue(u.get("secUid"), fuid,
                                        depth + 1, private=u.get("privateAccount"))
                    collected += 1
                if not d.get("hasMore") or not ulist:
                    complete = True
                    break
                nc = str(d.get("minCursor") or d.get("maxCursor") or "0")
                if self._cursor_anomaly(nc, seen_cursors, cursor):
                    if retries_used < self.page_retries:
                        retries_used += 1
                        sb.log(f"  [retry {retries_used}/{self.page_retries}] @{uid}: "
                               f"cursor anomaly (cursor={nc!r}) on page {page} - backing off.")
                        self._retry_sleep(retries_used)
                        continue
                    fail_reason = "cursor_anomaly"
                    break
                seen_cursors.add(nc)
                cursor = nc
                self._sleep()
        except KeyboardInterrupt:
            # Record the partial outcome before propagating so Ctrl+C keeps it.
            self._record_outcome(self._make_outcome(
                uid, depth, collected, page, attempts, False, "interrupted"))
            raise
        return self._make_outcome(uid, depth, collected, page, attempts,
                                  complete, fail_reason)

    @staticmethod
    def _cursor_anomaly(nc, seen_cursors, current):
        """A cursor is anomalous if it repeats a previously seen cursor or
        regresses (numerically goes backwards relative to the current one)."""
        if nc in seen_cursors:
            return True
        try:
            return int(nc) <= int(current)
        except (TypeError, ValueError):
            return False   # non-numeric cursors: only repetition counts

    @staticmethod
    def _make_outcome(uid, depth, collected, page, attempts, complete, fail_reason):
        if complete:
            status = "success"
        elif collected > 0:
            status = "partial"
        else:
            status = "failed"
        return {
            "account": uid,
            "depth": depth,
            "followers": collected,
            "pages": page,
            "complete": complete,
            "status": status,
            "fail_reason": fail_reason,
            "attempts": attempts,
            "time": now(),
        }

    # ---------- main loop ----------
    def run(self, keywords, seeds):
        for kw in keywords:
            self.seed_from_keyword(kw, depth=0)
        for s in seeds:
            self.seed_from_username(s, depth=0)

        sb.log(f"\n[{now()}] [start] going deep. frontier={len(self.frontier)} "
               f"| max_depth={self.max_depth} | max_users={self.max_users}")

        while self.frontier and self.users_processed < self.max_users:
            secUid, uid, depth = self.frontier.popleft()
            if uid in self.visited:
                continue
            self.users_processed += 1
            try:
                outcome = self.crawl_followers(secUid, uid, depth)
            except Exception as e:
                # One account's unexpected error must not kill the run or
                # clear the frontier - record it and keep going.
                sb.log(f"[{now()}] [warn] @{uid}: unexpected error: {e}")
                outcome = self._make_outcome(uid, depth, 0, 0, 0, False,
                                             f"exception:{type(e).__name__}")
            self._record_outcome(outcome)
            if outcome["complete"]:
                # Only fully-crawled accounts are marked done; partial/failed
                # accounts stay out of the state file so a later run retries them.
                self.visited.add(uid)
                self._dirty_since_save += 1
            sb.log(f"[{now()}] [{self.users_processed}] depth {depth} @{uid}: "
                   f"{outcome['followers']} follower(s) [{outcome['status']}] | "
                   f"frontier={len(self.frontier)} | numbers={self.phones_found}")
            if self._dirty_since_save >= 10:   # save state periodically
                self._save_state()
            self._sleep()

        self._save_state()
        sb.log(f"\n[{now()}] [done] frontier empty or limit reached. "
               f"users_processed={self.users_processed} | numbers={self.phones_found}")
        self.log_summary()


def main():
    cfg = sb.load_config()
    ap = argparse.ArgumentParser(description="Deep, continuous TikTok bio phone-number crawler")
    ap.add_argument("--keywords", nargs="*", default=cfg.get("keywords", []),
                    help="search keywords/hashtags to start from")
    ap.add_argument("--seeds", nargs="*", default=[],
                    help="seed accounts (usernames) to start from")
    ap.add_argument("--max-depth", type=int, default=cfg.get("max_depth", 4),
                    help="max expansion depth in the follower tree")
    ap.add_argument("--max-followers", type=int,
                    default=cfg.get("max_followers_per_user", 1000),
                    help="max followers fetched per account")
    ap.add_argument("--max-users", type=int, default=cfg.get("max_users", 1000000),
                    help="max accounts to process (safety cap)")
    ap.add_argument("--output", default=cfg.get("output", "output/tiktok-bio.json"))
    ap.add_argument("--state", default=cfg.get("state", "output/crawl_state.json"),
                    help="state file (users already crawled, to avoid re-crawling)")
    ap.add_argument("--outcomes", default=cfg.get("outcomes", "output/crawl_outcomes.jsonl"),
                    help="JSONL file with one outcome record per processed account")
    ap.add_argument("--page-retries", type=int, default=cfg.get("page_retries", 3),
                    help="max retries per account for a failed/anomalous followers page")
    ap.add_argument("--retry-backoff", type=float, default=cfg.get("retry_backoff", 1.0),
                    help="base seconds for exponential backoff between page retries")
    ap.add_argument("--reset-state", action="store_true",
                    help="ignore the state file and start fresh")
    ap.add_argument("--delay-min", type=float, default=cfg.get("delay_min", 0.3))
    ap.add_argument("--delay-max", type=float, default=cfg.get("delay_max", 1.0))
    args = ap.parse_args()

    if not args.keywords and not args.seeds:
        ap.error("Pass at least --keywords or --seeds.")

    out_path = args.output
    if not os.path.isabs(out_path):
        out_path = os.path.normpath(os.path.join(sb.HERE, out_path))

    state_path = args.state
    if not os.path.isabs(state_path):
        state_path = os.path.normpath(os.path.join(sb.HERE, state_path))
    if args.reset_state and os.path.exists(state_path):
        os.remove(state_path)
        sb.log("[state] removed state file (starting fresh).")

    outcomes_path = args.outcomes
    if outcomes_path and not os.path.isabs(outcomes_path):
        outcomes_path = os.path.normpath(os.path.join(sb.HERE, outcomes_path))

    if "sessionid=" not in cfg.get("cookie", ""):
        sb.log("[warn] no session (sessionid) in config.json.")

    crawler = DeepCrawler(
        cfg, out_path, args.max_depth, args.max_followers, args.max_users,
        delay_range=(args.delay_min, args.delay_max), state_path=state_path,
        outcomes_path=outcomes_path,
        page_retries=args.page_retries, retry_backoff=args.retry_backoff)

    try:
        crawler.run(args.keywords, args.seeds)
    except KeyboardInterrupt:
        crawler._save_state()
        sb.log(f"\n[{now()}] [stopped] manual stop (state + outcomes saved). "
               f"users={crawler.users_processed} | numbers={crawler.phones_found}")
        crawler.log_summary()
    sb.log(f"[file] {out_path}")
    sb.log(f"[state] {state_path}")
    if outcomes_path:
        sb.log(f"[outcomes] {outcomes_path}")


if __name__ == "__main__":
    main()
