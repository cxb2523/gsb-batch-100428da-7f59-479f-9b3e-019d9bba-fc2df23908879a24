"""云厂商采集三种抓取结局 + 合并落库行为的本地测试。

不访问网络：用 mock 替换 fetch_page，构造成功页 / 无 meta 页 / 请求失败三种场景。

运行：
    python scripts/test_cloud_collector.py
    或 python -m pytest scripts/test_cloud_collector.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cloud_collector as cc
from dedup import merge, normalize_activity

VENDOR, SECTION, URL = "华为云", "免费试用", "https://example.com/activity"
SID = cc._stable_id(URL)
TODAY = date(2026, 11, 15)

HTML_OK = (
    "<html><head>"
    "<title>华为云免费试用-华为云</title>"
    '<meta name="description" content="免费领取云服务器，0 元试用 AI 算力">'
    "</head><body></body></html>"
)
HTML_NO_META = "<html><head></head><body>访问验证，请稍候</body></html>"


def _old_entry(**overrides) -> dict:
    """模拟 activities.json 里已有的同 URL 旧条目（落库后的规范 schema）。"""
    e = {
        "id": SID,
        "title": f"{VENDOR}·{SECTION}",
        "category": "ai",
        "source_url": URL,
        "claim_url": URL,
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
        "value": "",
        "requirements": [],
        "description": "旧数据里的真实描述",
        "tags": [VENDOR, SECTION],
        "discovered_at": "2026-01-01T00:00:00+08:00",
        "last_seen": "2026-01-01",
        "status": "active",
    }
    e.update(overrides)
    return e


def _collect(html: str | None, old_acts: list[dict] | None) -> list[dict]:
    """用受控的单页清单 + 假 fetch 跑一遍云厂商采集。"""
    pages = [(VENDOR, SECTION, URL)]
    with mock.patch.object(cc, "CLOUD_PAGES", pages), \
         mock.patch.object(cc, "fetch_page", return_value=html), \
         mock.patch.object(cc.time, "sleep"):
        return cc.collect_cloud_activities(old_acts=old_acts)


class CollectOutcomeTest(unittest.TestCase):
    """采集层：三种结局标记 + 无旧数据不生成条目。"""

    def test_success_marks_fetched(self):
        acts = _collect(HTML_OK, None)
        self.assertEqual(len(acts), 1)
        a = acts[0]
        self.assertEqual(a["id"], SID)
        self.assertEqual(a["fetch_status"], cc.FETCH_OK)
        self.assertIn("免费领取云服务器", a["description"])
        self.assertEqual(a["tags"], [VENDOR, SECTION])

    def test_no_meta_page_fallback(self):
        acts = _collect(HTML_NO_META, [_old_entry()])
        self.assertEqual(len(acts), 1)
        a = acts[0]
        self.assertEqual(a["fetch_status"], cc.FETCH_FALLBACK_NO_META)
        self.assertEqual(a["id"], SID)  # 稳定 id 不变
        # 回退条目来自旧数据：描述、标签原样保留
        self.assertEqual(a["description"], "旧数据里的真实描述")
        self.assertEqual(a["tags"], [VENDOR, SECTION])
        # 仍按原规则顺延结束日期 30 天
        expected_end = (cc.BJ + timedelta(days=30)).strftime("%Y-%m-%d")
        self.assertEqual(a["end_date"], expected_end)

    def test_request_failure_fallback(self):
        acts = _collect(None, [_old_entry()])
        self.assertEqual(len(acts), 1)
        a = acts[0]
        self.assertEqual(a["fetch_status"], cc.FETCH_FALLBACK_ERROR)
        self.assertEqual(a["id"], SID)
        self.assertEqual(a["description"], "旧数据里的真实描述")

    def test_no_old_data_no_entry(self):
        # 请求失败 + 无旧数据 → 不凭空生成条目
        self.assertEqual(_collect(None, None), [])
        self.assertEqual(_collect(None, []), [])
        # 无 meta + 无旧数据 → 同样不生成
        self.assertEqual(_collect(HTML_NO_META, None), [])
        # 只有别的 URL 的旧数据 → 不能借来用
        self.assertEqual(_collect(None, [_old_entry(id="cloud_000000000000")]), [])


class MergeOutcomeTest(unittest.TestCase):
    """合并落库层：标记更新、字段保护、status 按日期重算。"""

    def _normalize(self, raw: dict) -> dict:
        act = normalize_activity(raw, "ai")
        self.assertIsNotNone(act)
        return act

    def test_fetch_status_survives_normalize(self):
        act = self._normalize({
            "id": SID, "title": f"{VENDOR}·{SECTION}", "url": URL,
            "description": "x", "fetch_status": cc.FETCH_FALLBACK_ERROR,
        })
        self.assertEqual(act["fetch_status"], cc.FETCH_FALLBACK_ERROR)

    def test_success_clears_old_fallback_marker(self):
        old = _old_entry(fetch_status=cc.FETCH_FALLBACK_ERROR)
        new = self._normalize({
            "id": SID, "title": f"{VENDOR}·{SECTION}", "url": URL,
            "description": "本次新抓的描述", "tags": [VENDOR, "新人特惠"],
            "start_date": "2026-11-01", "end_date": "2026-12-15",
            "fetch_status": cc.FETCH_OK,
        })
        merged = merge([old], [new], TODAY)
        self.assertEqual(len(merged), 1)
        m = merged[0]
        # 旧回退标记被本次成功抓取清除
        self.assertEqual(m["fetch_status"], cc.FETCH_OK)
        # 已有有效字段不被新条目覆盖（旧值优先）
        self.assertEqual(m["description"], "旧数据里的真实描述")
        self.assertEqual(m["tags"], [VENDOR, SECTION])

    def test_fallback_does_not_overwrite_fields(self):
        old = _old_entry()
        fb = _collect(None, [old])[0]
        self.assertEqual(fb["fetch_status"], cc.FETCH_FALLBACK_ERROR)
        fb["description"] = ""  # 模拟回退条目内容缺失
        fb["tags"] = []
        merged = merge([old], [self._normalize(fb)], TODAY)
        m = merged[0]
        # 回退不覆盖已有有效描述、标签
        self.assertEqual(m["description"], "旧数据里的真实描述")
        self.assertEqual(m["tags"], [VENDOR, SECTION])
        # 但回退标记如实落库
        self.assertEqual(m["fetch_status"], cc.FETCH_FALLBACK_ERROR)

    def test_fallback_not_forced_active_when_expired(self):
        # 库里带回退标记且结束日期已过的条目：状态刷新按日期判 ended
        old = _old_entry(end_date="2026-01-01", fetch_status=cc.FETCH_FALLBACK_ERROR)
        merged = merge([old], [], TODAY)
        self.assertEqual(merged[0]["status"], "ended")
        self.assertEqual(merged[0]["fetch_status"], cc.FETCH_FALLBACK_ERROR)

        # 新抓的回退条目即便原始数据里写着 active，合并时也按日期重算
        new = self._normalize({
            "id": SID, "title": f"{VENDOR}·{SECTION}", "url": URL,
            "description": "旧数据里的真实描述",
            "start_date": "2025-01-01", "end_date": "2026-01-01",
            "status": "active", "fetch_status": cc.FETCH_FALLBACK_NO_META,
        })
        merged2 = merge([], [new], TODAY)
        self.assertEqual(merged2[0]["status"], "ended")
        self.assertEqual(merged2[0]["fetch_status"], cc.FETCH_FALLBACK_NO_META)


if __name__ == "__main__":
    unittest.main(verbosity=2)
