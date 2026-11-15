#!/usr/bin/env python3
"""
Pure-API TikTok tool (no browser / no Selenium):
  Give a username -> fetch its followers directly via the API -> scan each
  follower's bio; if a phone number is found, save (name + number + country) to a
  JSON file.

How it works (manually signed HTTP requests):
  - We sign requests ourselves: X-Gnarly (via gnarly.py) + X-Bogus=1 + random
    msToken, and for search we add a captured (reusable) X-Dynosaur from config.json.
  - We use curl_cffi with browser impersonation to bypass Akamai's TLS
    fingerprinting (plain `requests` gets blocked and returns an empty body).
  - Endpoints:
      * user search:   /api/search/user/full/   (needs X-Dynosaur)
      * follower list: /api/user/list/?scene=67  (no X-Dynosaur)

Usage:
    python3 search_bio.py coderoot.ksa
    python3 search_bio.py user1 user2 --max-followers 1000
    python3 search_bio.py <username> --no-seed
"""

import sys
import io
import os
import json
import random
import string
import argparse
import urllib.parse as up

from curl_cffi import requests as creq

import phones
import country
import categories
from gnarly import get_X_Gnarly

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) "
      "Gecko/20100101 Firefox/152.0")
FOLLOWERS_SCENE = "67"   # full follower list in the web UI

BASE_PARAMS = {
    "aid": "1988", "app_language": "ar", "app_name": "tiktok_web",
    "channel": "tiktok_web", "cookie_enabled": "true", "device_platform": "web_pc",
    "os": "mac", "priority_region": "EG", "region": "EG",
    "tz_name": "Africa/Cairo", "user_is_login": "true",
}


def log(*a):
    print(*a, flush=True)


def load_config():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def parse_cookies(header: str) -> dict:
    out = {}
    for part in header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def rnd_mstoken() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits + "-_", k=128))


class TikTokAPI:
    def __init__(self, cfg):
        self.cookies = parse_cookies(cfg.get("cookie", ""))
        self.x_dynosaur = cfg.get("x_dynosaur", "")
        self.impersonate = cfg.get("impersonate", "firefox133")
        self.session = creq.Session()

    def _get(self, path, extra, referer, use_dynosaur=False):
        params = dict(BASE_PARAMS)
        params.update(extra)
        params["msToken"] = rnd_mstoken()
        qs = up.urlencode(params)
        gnarly = get_X_Gnarly(qs, "", UA)
        url = (f"https://www.tiktok.com{path}?{qs}"
               f"&X-Bogus=1&X-Gnarly={up.quote(gnarly)}")
        if use_dynosaur and self.x_dynosaur:
            url += f"&X-Dynosaur={up.quote(self.x_dynosaur)}"
        headers = {
            "User-Agent": UA, "Accept": "*/*",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            "Referer": referer, "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
        }
        try:
            r = self.session.get(url, headers=headers, cookies=self.cookies,
                                 impersonate=self.impersonate, timeout=25)
        except Exception as e:
            log(f"  [warn] connection error: {e}")
            return None
        if not r.content.strip():
            return None      # empty body = rejected (session/signature/block)
        try:
            return r.json()
        except Exception:
            return None

    def resolve_user(self, username):
        """Username -> {uniqueId, nickname, signature, secUid} via the search API."""
        username = username.lstrip("@").strip()
        d = self._get("/api/search/user/full/",
                      {"from_page": "search", "keyword": username, "offset": "0",
                       "cursor": "0", "search_id": "", "web_search_code": "{}"},
                      f"https://www.tiktok.com/search/user?q={up.quote(username)}",
                      use_dynosaur=True)
        if not d or not d.get("user_list"):
            return None
        users = d["user_list"]
        exact = next((x for x in users
                      if (x.get("user_info", {}).get("unique_id") or "").lower()
                      == username.lower()), None)
        ui = (exact or users[0]).get("user_info", {})
        if not ui.get("sec_uid"):
            return None
        return {
            "uniqueId": ui.get("unique_id") or username,
            "nickname": ui.get("nickname") or "",
            "signature": ui.get("signature") or "",
            "secUid": ui.get("sec_uid"),
        }

    def fetch_followers(self, secUid, max_followers, page_size):
        """Fetch followers via /api/user/list scene=67 with pagination."""
        collected = {}
        cursor = "0"
        seen = set()
        page_no = 0
        while len(collected) < max_followers:
            d = self._get("/api/user/list/",
                          {"from_page": "user", "secUid": secUid,
                           "count": str(page_size), "minCursor": cursor,
                           "maxCursor": "0", "scene": FOLLOWERS_SCENE},
                          "https://www.tiktok.com/")
            if not d:
                log("  [warn] empty response (session may have expired - refresh cookie/token).")
                break
            if d.get("status_code") not in (0, None):
                log(f"  [warn] status_code={d.get('status_code')}")
                break
            before = len(collected)
            for x in (d.get("userList") or []):
                u = x.get("user", {})
                uid = u.get("uniqueId")
                if uid:
                    collected[uid] = {
                        "uniqueId": uid,
                        "nickname": u.get("nickname") or "",
                        "signature": u.get("signature") or "",
                    }
            new = len(collected) - before
            page_no += 1
            log(f"  page {page_no}: +{new} (total {len(collected)}) "
                f"hasMore={d.get('hasMore')}")
            if not d.get("hasMore") or new == 0:
                break
            nc = str(d.get("minCursor") or d.get("maxCursor") or "0")
            if nc in seen or nc == "0":
                break
            seen.add(nc)
            cursor = nc
        return list(collected.values())[:max_followers]


def build_records(users, source):
    records = []
    for u in users:
        bio = u.get("signature", "")
        name = u.get("nickname") or u.get("uniqueId")
        cat = categories.classify(bio, name)
        for num in phones.extract_phones(bio):
            c = country.detect_country(num, bio)
            records.append({
                "name": name,
                "username": u.get("uniqueId"),
                "phone": num,
                "country": c["country"],
                "country_ar": c["country_ar"],
                "country_iso": c["iso"],
                "dial_code": c["dial_code"],
                "country_confidence": c["confidence"],
                "country_probs": c["probabilities"],
                "category": cat["category"],
                "category_ar": cat["category_ar"],
                "category_confidence": cat["confidence"],
                "category_tags": cat["tags"],
                "bio": bio,
                "source": source,
            })
    return records


def save_results(records, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    existing = []
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
        except Exception:
            existing = []
    seen = {(r.get("username"), r.get("phone")) for r in existing}
    added = 0
    for r in records:
        key = (r["username"], r["phone"])
        if key in seen:
            continue
        seen.add(key)
        existing.append(r)
        added += 1
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    return added, len(existing)


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(
        description="Pure API: fetch a TikTok user's followers and extract phone numbers from bios")
    ap.add_argument("usernames", nargs="*", help="one or more usernames")
    ap.add_argument("--max-followers", type=int,
                    default=cfg.get("max_followers_per_user", 2000))
    ap.add_argument("--page-size", type=int, default=cfg.get("page_size", 30))
    ap.add_argument("--output", default=cfg.get("output", "output/tiktok-bio.json"))
    ap.add_argument("--no-seed", action="store_true",
                    help="do not include the seed account's own number")
    args = ap.parse_args()

    usernames = [u.lstrip("@") for u in args.usernames]
    if not usernames:
        ap.error("Pass at least one username. Example: python3 search_bio.py coderoot.ksa")

    if "sessionid=" not in cfg.get("cookie", ""):
        log("[warn] no session (sessionid) in config.json - followers require login.")
    if not cfg.get("x_dynosaur"):
        log("[warn] no x_dynosaur in config.json - user search may fail.")

    out_path = args.output
    if not os.path.isabs(out_path):
        out_path = os.path.normpath(os.path.join(HERE, out_path))

    api = TikTokAPI(cfg)
    total_records = 0

    for i, uname in enumerate(usernames, 1):
        log(f"\n===== ({i}/{len(usernames)}) @{uname} =====")
        info = api.resolve_user(uname)
        if not info:
            log(f"  [warn] could not resolve @{uname} (missing? or refresh cookie/x_dynosaur).")
            continue
        log(f"  [ok] name={info['nickname']} | secUid={info['secUid'][:20]}...")

        if not args.no_seed:
            seed_recs = build_records([info], source=f"seed:@{uname}")
            if seed_recs:
                save_results(seed_recs, out_path)
                total_records += len(seed_recs)
                log(f"  + {len(seed_recs)} number(s) from the seed bio: "
                    f"{[r['phone'] for r in seed_recs]}")

        log(f"[followers] fetching followers of @{uname} (up to {args.max_followers})...")
        followers = api.fetch_followers(info["secUid"], args.max_followers, args.page_size)
        log(f"[followers] total: {len(followers)}")

        recs = build_records(followers, source=f"follower_of:@{uname}")
        added, total = save_results(recs, out_path)
        total_records += len(recs)
        log(f"  -> {len(recs)} number(s) in follower bios | new {added} | file total {total}")

    log(f"\n[done] file: {out_path}")
    log(f"   total numbers extracted this run: {total_records}")


if __name__ == "__main__":
    main()
