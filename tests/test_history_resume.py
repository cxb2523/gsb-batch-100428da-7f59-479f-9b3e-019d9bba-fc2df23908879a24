import json
import unittest
from unittest.mock import patch

from app.history_client import (
    fetch_history_days,
    merge_articles_with_sightings,
    merge_resumed_articles,
    plan_history_resume,
    resume_plan_from_state,
    resolve_history_start_offset,
)
from app.history_export import articles_for_list_export, render_export


def _page(offset: int, count: int = 8, *, more: bool = True) -> dict:
    return {
        "ok": True,
        "articles": [
            {
                "title": f"p{offset}",
                "link": f"https://mp.weixin.qq.com/s/{offset}",
                "publish_ts": 1_700_000_000 - offset,
                "mid": str(offset),
                "idx": "1",
                "sn": "s",
            }
        ],
        "can_continue": more,
        "next_offset": offset + count,
        "raw": {"general_msg_list": json.dumps({"list": [{}]})},
    }


def _sighting(
    mid: str,
    *,
    biz: str = "x",
    publish_ts: int = 1_700_000_000,
    title: str = "",
) -> dict:
    return {
        "title": title or f"sighting-{mid}",
        "link": f"https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx=1&sn=abc",
        "publish_ts": publish_ts,
        "mid": mid,
        "idx": "1",
        "sn": "abc",
        "__biz": biz,
        "source": "manual",
    }


class ResolveResumeOffsetTests(unittest.TestCase):
    def test_no_resume_starts_at_zero(self):
        self.assertEqual(
            resolve_history_start_offset(biz="A"),
            0,
        )

    def test_all_history_resumes_same_account(self):
        self.assertEqual(
            resolve_history_start_offset(
                biz="A",
                resume_biz="A",
                resume_offset=800,
                days=None,
            ),
            800,
        )

    def test_other_account_does_not_resume(self):
        self.assertEqual(
            resolve_history_start_offset(
                biz="B",
                resume_biz="A",
                resume_offset=800,
                days=None,
            ),
            0,
        )

    def test_older_date_range_resumes(self):
        self.assertEqual(
            resolve_history_start_offset(
                biz="A",
                resume_biz="A",
                resume_offset=800,
                resume_oldest_ts=1_700_000_000,
                start_ts=1_600_000_000,
                end_ts=1_650_000_000,
            ),
            800,
        )

    def test_newer_date_range_restarts(self):
        self.assertEqual(
            resolve_history_start_offset(
                biz="A",
                resume_biz="A",
                resume_offset=800,
                resume_oldest_ts=1_600_000_000,
                start_ts=1_700_000_000,
                end_ts=1_800_000_000,
            ),
            0,
        )


class FetchResumeTests(unittest.TestCase):
    @patch("app.history_client.fetch_getmsg_page")
    def test_uses_start_offset_and_returns_next(self, mock_fetch):
        seen: list[int] = []

        def fake(cred, offset=0, count=8, **kwargs):
            seen.append(offset)
            return _page(offset, count, more=True)

        mock_fetch.side_effect = fake
        result = fetch_history_days(
            {"__biz": "x", "uin": "1", "key": "k"},
            days=None,
            max_pages=1,
            count=8,
            start_offset=80,
            sleep_s=0,
            cooldown_every=0,
        )
        self.assertEqual(seen, [80])
        self.assertTrue(result.get("hit_page_cap"))
        self.assertEqual(result.get("next_offset"), 88)
        self.assertEqual(result.get("start_offset"), 80)

    def test_merge_keeps_older_pages(self):
        merged = merge_resumed_articles(
            [{"title": "new", "mid": "1", "idx": "1", "sn": "a"}],
            [{"title": "old", "mid": "2", "idx": "1", "sn": "b"}],
        )
        titles = {a["title"] for a in merged}
        self.assertEqual(titles, {"new", "old"})


class PageCapResumePayloadTests(unittest.TestCase):
    """翻页上限后返回的续翻信息必须足以让下一次点击接上进度。"""

    @patch("app.history_client.fetch_getmsg_page")
    def test_page_cap_returns_resume_payload(self, mock_fetch):
        def fake(cred, offset=0, count=8, **kwargs):
            return _page(offset, count, more=True)

        mock_fetch.side_effect = fake
        result = fetch_history_days(
            {"__biz": "biz-A", "uin": "1", "key": "k"},
            days=None,
            max_pages=2,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            sightings=[_sighting("s1", biz="biz-A")],
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["hit_page_cap"])
        self.assertTrue(result["can_continue"])
        # Legacy fields stay present and unchanged in meaning.
        for key in (
            "ok",
            "error",
            "articles",
            "pages",
            "warning",
            "merged_sightings",
            "stopped_reason",
            "next_offset",
            "can_continue",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["next_offset"], 16)
        self.assertEqual(result["stopped_reason"], "completed")

        resume = result["resume"]
        self.assertEqual(resume["start_offset"], 16)
        self.assertEqual(resume["next_offset"], 16)
        self.assertEqual(resume["oldest_ts"], result["scanned_oldest_ts"])
        self.assertIsNotNone(resume["oldest_ts"])
        self.assertEqual(resume["biz"], "biz-A")
        self.assertEqual(resume["__biz"], "biz-A")
        self.assertEqual(resume["merged_articles"], len(result["articles"]))
        self.assertEqual(resume["article_count"], len(result["articles"]))
        # 补录被合并进文章列表，并记下 key 供续翻去重。
        self.assertEqual(result["merged_sightings"], 1)
        self.assertIn("mid:s1|idx:1|sn:abc", resume["sighting_keys"])

        # 下一次续翻：直接回传上次结果即可接上 offset。
        plan = resume_plan_from_state(result["resume"], biz="biz-A", days=None)
        self.assertEqual(plan["start_offset"], 16)
        self.assertFalse(plan["restarted"])

    @patch("app.history_client.fetch_getmsg_page")
    def test_resume_continues_from_previous_offset(self, mock_fetch):
        seen: list[int] = []

        def fake(cred, offset=0, count=8, **kwargs):
            seen.append(offset)
            return _page(offset, count, more=True)

        mock_fetch.side_effect = fake
        cred = {"__biz": "biz-A", "uin": "1", "key": "k"}
        first = fetch_history_days(
            cred, days=None, max_pages=1, count=8, sleep_s=0, cooldown_every=0
        )
        self.assertEqual(first["next_offset"], 8)

        second = fetch_history_days(
            cred,
            days=None,
            max_pages=1,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            resume=first["resume"],
        )
        self.assertEqual(seen, [0, 8])
        self.assertEqual(second["start_offset"], 8)
        self.assertFalse(second["resume_restarted"])
        self.assertEqual(second["next_offset"], 16)


class ResumeFallbackTests(unittest.TestCase):
    """__biz 变化或窗口越界时必须自动从第 0 页重来。"""

    def test_plan_restarts_when_biz_changed(self):
        plan = plan_history_resume(
            biz="biz-B",
            resume_biz="biz-A",
            resume_offset=800,
            resume_oldest_ts=1_700_000_000,
            days=None,
        )
        self.assertEqual(plan["start_offset"], 0)
        self.assertTrue(plan["restarted"])
        self.assertEqual(plan["reason"], "biz_changed")

    def test_plan_restarts_when_already_older_than_window(self):
        plan = plan_history_resume(
            biz="biz-A",
            resume_biz="biz-A",
            resume_offset=800,
            resume_oldest_ts=1_600_000_000,
            start_ts=1_700_000_000,
            end_ts=1_800_000_000,
        )
        self.assertEqual(plan["start_offset"], 0)
        self.assertTrue(plan["restarted"])
        self.assertEqual(plan["reason"], "older_than_range_start")

    @patch("app.history_client.fetch_getmsg_page")
    def test_fetch_restarts_at_zero_when_biz_changed(self, mock_fetch):
        seen: list[int] = []

        def fake(cred, offset=0, count=8, **kwargs):
            seen.append(offset)
            return _page(offset, count, more=True)

        mock_fetch.side_effect = fake
        stale = {
            "biz": "biz-A",
            "__biz": "biz-A",
            "next_offset": 800,
            "start_offset": 800,
            "oldest_ts": 1_690_000_000,
        }
        result = fetch_history_days(
            {"__biz": "biz-B", "uin": "1", "key": "k"},
            days=None,
            max_pages=1,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            resume=stale,
        )
        self.assertEqual(seen, [0])
        self.assertTrue(result["resume_restarted"])
        self.assertEqual(result["resume_reason"], "biz_changed")
        self.assertEqual(result["start_offset"], 0)
        self.assertEqual(result["resume"]["biz"], "biz-B")

    @patch("app.history_client.fetch_getmsg_page")
    def test_fetch_restarts_at_zero_when_window_moved_past_scan(self, mock_fetch):
        seen: list[int] = []

        def fake(cred, offset=0, count=8, **kwargs):
            seen.append(offset)
            return _page(offset, count, more=True)

        mock_fetch.side_effect = fake
        stale = {
            "biz": "biz-A",
            "next_offset": 800,
            "oldest_ts": 1_600_000_000,
        }
        result = fetch_history_days(
            {"__biz": "biz-A", "uin": "1", "key": "k"},
            start_ts=1_700_000_000,
            end_ts=1_800_000_000,
            max_pages=1,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            resume=stale,
        )
        self.assertEqual(seen, [0])
        self.assertTrue(result["resume_restarted"])
        self.assertEqual(result["resume_reason"], "older_than_range_start")


class SightingsOnResumeTests(unittest.TestCase):
    """续翻时补录仍合并去重，但不得重复计入 merged_sightings。"""

    @patch("app.history_client.fetch_getmsg_page")
    def test_sightings_not_recounted_on_resume(self, mock_fetch):
        def fake(cred, offset=0, count=8, **kwargs):
            return _page(offset, count, more=True)

        mock_fetch.side_effect = fake
        cred = {"__biz": "biz-A", "uin": "1", "key": "k"}
        sightings = [
            _sighting("s1", biz="biz-A", publish_ts=1_700_000_000),
            _sighting("s2", biz="biz-A", publish_ts=1_699_999_000),
            _sighting("other-biz", biz="biz-Z", publish_ts=1_700_000_000),
        ]
        first = fetch_history_days(
            cred,
            days=None,
            max_pages=1,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            sightings=sightings,
        )
        # 其它 __biz 的补录被过滤掉，不进入结果。
        self.assertEqual(first["merged_sightings"], 2)
        self.assertNotIn(
            "other-biz", {str(a.get("mid") or "") for a in first["articles"]}
        )

        second = fetch_history_days(
            cred,
            days=None,
            max_pages=1,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            sightings=sightings,
            resume=first["resume"],
        )
        self.assertTrue(second["ok"])
        # 同一条补录不会在续翻里再次计入。
        self.assertEqual(second["merged_sightings"], 0)
        self.assertEqual(
            second["resume"]["sighting_keys"], first["resume"]["sighting_keys"]
        )

    @patch("app.history_client.fetch_getmsg_page")
    def test_new_sighting_on_resume_is_counted(self, mock_fetch):
        def fake(cred, offset=0, count=8, **kwargs):
            return _page(offset, count, more=True)

        mock_fetch.side_effect = fake
        cred = {"__biz": "biz-A", "uin": "1", "key": "k"}
        first = fetch_history_days(
            cred,
            days=None,
            max_pages=1,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            sightings=[_sighting("s1", biz="biz-A")],
        )
        second = fetch_history_days(
            cred,
            days=None,
            max_pages=1,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            sightings=[
                _sighting("s1", biz="biz-A"),
                _sighting("s2", biz="biz-A"),
            ],
            resume=first["resume"],
        )
        self.assertEqual(second["merged_sightings"], 1)
        keys = set(second["resume"]["sighting_keys"])
        self.assertIn("mid:s1|idx:1|sn:abc", keys)
        self.assertIn("mid:s2|idx:1|sn:abc", keys)

    def test_merge_collects_keys_and_filters_biz(self):
        keys: set[str] = set()
        merged = merge_articles_with_sightings(
            [],
            [
                _sighting("s1", biz="biz-A"),
                _sighting("s2", biz="biz-B"),
            ],
            biz="biz-A",
            collect_sighting_keys=keys,
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(keys, {"mid:s1|idx:1|sn:abc"})

    @patch("app.history_client.fetch_getmsg_page")
    def test_restart_does_not_suppress_sighting_counts(self, mock_fetch):
        """从头重来时，旧 key 不应让本次补录计数变成 0。"""

        def fake(cred, offset=0, count=8, **kwargs):
            return _page(offset, count, more=True)

        mock_fetch.side_effect = fake
        stale = {
            "biz": "biz-A",
            "next_offset": 800,
            "oldest_ts": 1_600_000_000,
            "sighting_keys": ["mid:s1|idx:1|sn:abc"],
        }
        result = fetch_history_days(
            {"__biz": "biz-A", "uin": "1", "key": "k"},
            start_ts=1_700_000_000,
            end_ts=1_800_000_000,
            max_pages=1,
            count=8,
            sleep_s=0,
            cooldown_every=0,
            sightings=[_sighting("s1", biz="biz-A", publish_ts=1_700_000_000)],
            resume=stale,
        )
        self.assertTrue(result["resume_restarted"])
        self.assertEqual(result["merged_sightings"], 1)


class ListExportSelectionTests(unittest.TestCase):
    def test_export_only_checked_articles(self):
        articles = [
            {"title": "a", "link": "https://a", "identity": "a"},
            {"title": "b", "link": "https://b", "identity": "b"},
            {"title": "c", "link": "https://c", "identity": "c"},
        ]
        picked = articles_for_list_export(articles, {"b"})
        self.assertEqual([a["title"] for a in picked], ["b"])
        text = render_export(picked, fmt="links")
        self.assertIn("https://b", text)
        self.assertNotIn("https://a", text)
        self.assertNotIn("https://c", text)

    def test_export_empty_when_nothing_checked(self):
        articles = [{"title": "a", "link": "https://a", "identity": "a"}]
        self.assertEqual(articles_for_list_export(articles, set()), [])


if __name__ == "__main__":
    unittest.main()
