#!/usr/bin/env python3
"""
Acceptance tests for deep_search.py resilience — uses a FAKE TikTok API
(no network, no real credentials) to simulate:

  1. normal pagination                     -> success, account in state file
  2. empty response, then retry succeeds   -> success, attempts counted
  3. persistent empty responses            -> failed, retries exhausted, NOT in state
  4. persistent non-zero status_code       -> failed, reason=status_code, NOT in state
  5. repeated cursor                       -> partial, followers kept, NOT in state
  6. KeyboardInterrupt mid-pagination      -> outcome recorded, progress saved
  7. one failing account + one healthy     -> the healthy one still completes
  8. partial failure still scans/saves bios of already-fetched followers

Run:  python3 test_deep_search_resilience.py
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import deep_search as ds


def make_page(users, has_more, cursor, status_code=0):
    """Build a fake /api/user/list/ response."""
    return {
        "status_code": status_code,
        "hasMore": has_more,
        "minCursor": str(cursor),
        "maxCursor": "0",
        "userList": [
            {"user": {"uniqueId": u, "secUid": "SEC_" + u, "nickname": u,
                      "signature": sig, "privateAccount": False}}
            for u, sig in users
        ],
    }


def simple_page(n, has_more, cursor, prefix="f", sig=""):
    return make_page([(f"{prefix}{i}", sig) for i in range(n)], has_more, cursor)


class FakeAPI:
    """Scripted replacement for TikTokAPI._get.

    `script` maps minCursor -> response dict | None | Exception instance |
    list of the above (consumed one per call).
    """

    def __init__(self, script):
        self.script = script
        self.calls = []          # list of minCursor values actually requested

    def _get(self, path, extra, referer, use_dynosaur=False):
        cursor = extra.get("minCursor", "0")
        self.calls.append(cursor)
        item = self.script.get(cursor)
        if isinstance(item, list):
            item = item.pop(0) if item else None
        if isinstance(item, BaseException):
            raise item
        return item


class ResilienceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.out_path = os.path.join(d, "out", "tiktok-bio.json")
        self.state_path = os.path.join(d, "out", "crawl_state.json")
        self.outcomes_path = os.path.join(d, "out", "crawl_outcomes.jsonl")
        self.crawler = ds.DeepCrawler(
            {}, self.out_path, max_depth=3, max_followers=1000, max_users=100,
            delay_range=(0, 0), state_path=self.state_path,
            outcomes_path=self.outcomes_path, page_retries=2, retry_backoff=0)
        self._quiet = redirect_stdout(io.StringIO())
        self._quiet.__enter__()

    def tearDown(self):
        self._quiet.__exit__(None, None, None)
        self.tmp.cleanup()

    def install(self, script):
        fake = FakeAPI(script)
        self.crawler.api._get = fake._get
        return fake

    def read_outcomes(self):
        with open(self.outcomes_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def read_state(self):
        with open(self.state_path, encoding="utf-8") as f:
            return set(json.load(f)["crawled"])

    # 1 ------------------------------------------------------------------
    def test_normal_pagination_success(self):
        self.install({
            "0": simple_page(30, has_more=True, cursor="100"),
            "100": simple_page(30, has_more=False, cursor="200", prefix="g"),
        })
        oc = self.crawler.crawl_followers("SEC_seed", "seed", 1)
        self.assertTrue(oc["complete"])
        self.assertEqual(oc["status"], "success")
        self.assertEqual(oc["followers"], 60)
        self.assertIsNone(oc["fail_reason"])
        self.assertEqual(oc["attempts"], 2)

    # 2 ------------------------------------------------------------------
    def test_empty_response_then_retry_succeeds(self):
        self.install({
            "0": [None, simple_page(10, has_more=False, cursor="50")],
        })
        oc = self.crawler.crawl_followers("SEC_seed", "seed", 1)
        self.assertTrue(oc["complete"])
        self.assertEqual(oc["status"], "success")
        self.assertEqual(oc["followers"], 10)
        self.assertEqual(oc["attempts"], 2)   # 1 failed try + 1 good page

    # 3 ------------------------------------------------------------------
    def test_persistent_empty_responses_fail_bounded(self):
        fake = self.install({"0": None})
        oc = self.crawler.crawl_followers("SEC_seed", "seed", 1)
        self.assertFalse(oc["complete"])
        self.assertEqual(oc["status"], "failed")
        self.assertEqual(oc["fail_reason"], "empty_response")
        self.assertEqual(oc["followers"], 0)
        # bounded: 1 initial attempt + page_retries(2) retries, then stop
        self.assertEqual(oc["attempts"], 3)
        self.assertEqual(len(fake.calls), 3)

    # 4 ------------------------------------------------------------------
    def test_persistent_status_code_failure(self):
        self.install({"0": make_page([], has_more=True, cursor="0", status_code=8)})
        oc = self.crawler.crawl_followers("SEC_seed", "seed", 1)
        self.assertEqual(oc["status"], "failed")
        self.assertEqual(oc["fail_reason"], "status_code")
        self.assertEqual(oc["attempts"], 3)

    # 5 ------------------------------------------------------------------
    def test_repeated_cursor_is_partial_and_keeps_followers(self):
        self.install({
            "0": simple_page(30, has_more=True, cursor="100"),
            "100": simple_page(30, has_more=True, cursor="100", prefix="g"),  # repeats
        })
        oc = self.crawler.crawl_followers("SEC_seed", "seed", 1)
        self.assertFalse(oc["complete"])
        self.assertEqual(oc["status"], "partial")
        self.assertEqual(oc["fail_reason"], "cursor_anomaly")
        self.assertEqual(oc["followers"], 60)   # everything fetched so far is kept

    # 6 ------------------------------------------------------------------
    def test_keyboard_interrupt_records_outcome_and_reraises(self):
        self.install({
            "0": simple_page(30, has_more=True, cursor="100"),
            "100": KeyboardInterrupt(),
        })
        with self.assertRaises(KeyboardInterrupt):
            self.crawler.crawl_followers("SEC_seed", "seed", 1)
        outcomes = self.read_outcomes()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["fail_reason"], "interrupted")
        self.assertEqual(outcomes[0]["status"], "partial")
        self.assertEqual(outcomes[0]["followers"], 30)
        self.assertNotIn("seed", self.crawler.visited)

    # 7 + 8 --------------------------------------------------------------
    def test_run_isolates_failures_and_summarizes(self):
        # frontier: "bad" always fails; "good" pages fine and has a phone in a bio
        self.crawler.max_depth = 1   # keep the frontier from expanding further
        good_page = make_page(
            [("rich", "للتواصل 0501234567"), ("plain", "")],
            has_more=False, cursor="10")
        self.crawler.frontier.append(("SEC_bad", "bad", 1))
        self.crawler.frontier.append(("SEC_good", "good", 1))
        self.crawler.enqueued.update({"bad", "good"})

        def fake_get(path, extra, referer, use_dynosaur=False):
            if extra.get("secUid") == "SEC_bad":
                return None
            return good_page
        self.crawler.api._get = fake_get

        self.crawler.run(keywords=[], seeds=[])

        # summary: one failed, one success
        self.assertEqual(self.crawler.summary["failed"], 1)
        self.assertEqual(self.crawler.summary["success"], 1)
        self.assertEqual(self.crawler.summary["partial"], 0)

        # state file: only the fully-crawled account is marked done
        self.assertEqual(self.read_state(), {"good"})

        # outcome records: one per account, on disk
        outcomes = self.read_outcomes()
        self.assertEqual(len(outcomes), 2)
        by_acc = {o["account"]: o for o in outcomes}
        self.assertEqual(by_acc["bad"]["status"], "failed")
        self.assertEqual(by_acc["bad"]["fail_reason"], "empty_response")
        self.assertEqual(by_acc["good"]["status"], "success")
        self.assertTrue(all("attempts" in o and "depth" in o for o in outcomes))

        # bio of the healthy account's follower was scanned and saved
        with open(self.out_path, encoding="utf-8") as f:
            recs = json.load(f)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["username"], "rich")
        self.assertEqual(recs[0]["source"], "follower_of:@good")

    # 8b -----------------------------------------------------------------
    def test_partial_failure_still_saves_collected_bios(self):
        self.install({
            "0": make_page([("rich", "جوال 0501234567")], has_more=True, cursor="100"),
            "100": None,   # then the API dies for good
        })
        oc = self.crawler.crawl_followers("SEC_seed", "seed", 1)
        self.assertEqual(oc["status"], "partial")
        self.assertEqual(oc["fail_reason"], "empty_response")
        with open(self.out_path, encoding="utf-8") as f:
            recs = json.load(f)
        self.assertEqual([r["username"] for r in recs], ["rich"])

    # Ctrl+C during run(): progress + outcomes are preserved --------------
    def test_interrupt_during_run_saves_progress(self):
        self.crawler.max_depth = 1
        self.crawler.frontier.append(("SEC_done", "done", 1))
        self.crawler.frontier.append(("SEC_cut", "cut", 1))
        self.crawler.enqueued.update({"done", "cut"})

        def fake_get(path, extra, referer, use_dynosaur=False):
            if extra.get("secUid") == "SEC_done":
                return simple_page(5, has_more=False, cursor="7")
            if extra.get("minCursor") == "0":
                return simple_page(5, has_more=True, cursor="50", prefix="cut")
            raise KeyboardInterrupt
        self.crawler.api._get = fake_get

        with self.assertRaises(KeyboardInterrupt):
            self.crawler.run(keywords=[], seeds=[])

        # main()'s handler does exactly this:
        self.crawler._save_state()
        self.crawler.log_summary()

        # the completed account is in the state file; the interrupted one is not
        self.assertEqual(self.read_state(), {"done"})
        # both accounts left an outcome record on disk
        outcomes = {o["account"]: o for o in self.read_outcomes()}
        self.assertEqual(outcomes["done"]["status"], "success")
        self.assertEqual(outcomes["cut"]["fail_reason"], "interrupted")
        self.assertEqual(self.crawler.summary["success"], 1)
        self.assertEqual(self.crawler.summary["partial"], 1)

    # state file format unchanged -----------------------------------------
    def test_state_file_format_unchanged(self):
        self.crawler.visited.add("done_user")
        self.crawler._save_state()
        with open(self.state_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data, {"crawled": ["done_user"]})

    # unexpected exception in one account does not clear the frontier -----
    def test_unexpected_exception_does_not_stop_run(self):
        self.crawler.max_depth = 1
        self.crawler.frontier.append(("SEC_boom", "boom", 1))
        self.crawler.frontier.append(("SEC_ok", "ok", 1))
        self.crawler.enqueued.update({"boom", "ok"})

        def fake_get(path, extra, referer, use_dynosaur=False):
            if extra.get("secUid") == "SEC_boom":
                raise RuntimeError("boom")
            return simple_page(5, has_more=False, cursor="7")
        self.crawler.api._get = fake_get

        self.crawler.run(keywords=[], seeds=[])
        self.assertEqual(self.crawler.summary["failed"], 1)
        self.assertEqual(self.crawler.summary["success"], 1)
        self.assertEqual(self.read_state(), {"ok"})
        outcomes = {o["account"]: o for o in self.read_outcomes()}
        self.assertTrue(outcomes["boom"]["fail_reason"].startswith("exception:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
