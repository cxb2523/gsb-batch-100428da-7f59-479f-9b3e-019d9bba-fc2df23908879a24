"""采集降级来源标记的验收测试 (离线, 不访问真实抖音).

覆盖:
  A1 浏览器异常 -> HTTP 回落, 结果与落库均带降级标记和原因
  A2 HTTP 403 / 验证码文本时保留风控错误
  A3 正常浏览器采集不被标为降级
  A4 快照接口与 CSV 导出在新增字段后保持兼容

依赖缺失 (playwright/loguru/fastapi 等) 时跳过, 不阻塞其它测试。
"""
import asyncio
import csv
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LITE_DIR = os.path.join(REPO_ROOT, "lite")

try:
    if LITE_DIR not in sys.path:
        sys.path.insert(0, LITE_DIR)
    import collector as collector_mod
    from collector import DouyinLiveCollector
    from database import Database
except Exception as exc:  # pragma: no cover - depends on optional deps
    DouyinLiveCollector = None
    Database = None
    _IMPORT_ERROR = exc
finally:
    # 避免把 lite/ 的模块名 (app/collector/database) 泄漏给其它测试
    if LITE_DIR in sys.path:
        sys.path.remove(LITE_DIR)

ROOM_URL = "https://live.douyin.com/123456789"

LIVE_HTML = (
    "<html><head><title>某某的抖音直播间 - 抖音直播</title></head>"
    "<body><div>在线观众 · 1,234</div><div>2.3万本场点赞</div></body></html>"
)
PARTIAL_HTML = (
    "<html><head><title>部分主播的抖音直播间 - 抖音直播</title></head>"
    "<body><div>在线观众 888</div></body></html>"
)


async def _no_sleep(*args, **kwargs):
    return None


class _FakePage:
    def __init__(self, html="", title="", body_text="", exc=None):
        self.html = html
        self._title = title
        self.body_text = body_text
        self.exc = exc

    async def goto(self, url, **kwargs):
        if self.exc:
            raise self.exc

    async def evaluate(self, js):
        if "innerText" in js:
            return self.body_text
        return None

    async def content(self):
        return self.html

    async def title(self):
        return self._title


class _FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeHTTP:
    def __init__(self, page_response):
        self.page_response = page_response

    async def get(self, url, headers=None, **kwargs):
        if "room/info" in url:
            return _FakeResponse(403, "")
        return self.page_response


def _make_collector(page=None, http_response=None, browser_mode="cdp"):
    collector = DouyinLiveCollector(browser_mode=browser_mode)
    collector._page = page
    collector._http_client = _FakeHTTP(http_response or _FakeResponse(200, LIVE_HTML))
    return collector


@unittest.skipIf(DouyinLiveCollector is None, "lite 采集器依赖缺失")
class CollectorDegradeTest(unittest.TestCase):
    """采集链路: 降级标记与原因."""

    def _fetch(self, page, http_response=None):
        collector = _make_collector(page=page, http_response=http_response)
        with mock.patch.object(collector_mod.asyncio, "sleep", new=_no_sleep):
            return asyncio.run(collector.fetch_live_data(ROOM_URL))

    def test_browser_success_not_degraded(self):
        page = _FakePage(
            LIVE_HTML,
            "某某的抖音直播间 - 抖音直播",
            "在线观众 · 1,234 2.3万本场点赞",
        )
        result = self._fetch(page)

        self.assertEqual(result["source"], "browser")
        self.assertFalse(result["degraded"])
        self.assertIsNone(result["degrade_reason"])
        self.assertEqual(result["viewers"], 1234)
        self.assertTrue(result["is_live"])

    def test_browser_timeout_falls_back_with_reason(self):
        result = self._fetch(_FakePage(exc=asyncio.TimeoutError()))

        self.assertEqual(result["source"], "http")
        self.assertTrue(result["degraded"])
        self.assertIn("timeout", result["degrade_reason"])
        # 回落得到的数据仍要保留
        self.assertEqual(result["viewers"], 1234)
        self.assertTrue(result["is_live"])

    def test_browser_navigation_error_falls_back_with_reason(self):
        result = self._fetch(
            _FakePage(exc=RuntimeError("net::ERR_CONNECTION_RESET at navigation"))
        )

        self.assertTrue(result["degraded"])
        self.assertIn("navigation_error", result["degrade_reason"])

    def test_http_403_keeps_risk_error_after_fallback(self):
        result = self._fetch(_FakePage(exc=RuntimeError("boom")), _FakeResponse(403, ""))

        self.assertEqual(result["error"], "HTTP 403 被风控")
        self.assertTrue(result["degraded"])
        self.assertEqual(result["source"], "http")

    def test_captcha_text_keeps_risk_error(self):
        captcha_html = "<html><body>请完成安全验证 滑块</body></html>"
        result = self._fetch(_FakePage(exc=RuntimeError("page changed")), _FakeResponse(200, captcha_html))

        self.assertEqual(result["error"], "触发了验证码")
        self.assertTrue(result["degraded"])

    def test_partial_metrics_keep_parsed_fields(self):
        page = _FakePage(PARTIAL_HTML, "部分主播的抖音直播间 - 抖音直播", "在线观众 888")
        result = self._fetch(page)

        self.assertEqual(result["viewers"], 888)
        self.assertIsNone(result["likes"])
        self.assertTrue(result["title"])
        self.assertFalse(result["degraded"])

    def test_http_generic_title_not_treated_as_live(self):
        # 未开播页标题只有站点名, 不能因含 "抖音直播" 被误判为直播
        offline_html = "<html><head><title>抖音直播</title></head><body>未开播</body></html>"
        collector = _make_collector(page=None, browser_mode="http",
                                    http_response=_FakeResponse(200, offline_html))
        result = asyncio.run(collector.fetch_live_data(ROOM_URL))

        self.assertFalse(result["is_live"])
        self.assertFalse(result["degraded"])
        self.assertEqual(result["source"], "http")


@unittest.skipIf(DouyinLiveCollector is None, "lite 采集器依赖缺失")
class SnapshotStorageTest(unittest.TestCase):
    """落库、快照接口与 CSV 导出."""

    def test_migration_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "monitor.db")
            old_conn = sqlite3.connect(db_path)
            old_conn.executescript(
                """
                CREATE TABLE live_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL, url TEXT NOT NULL,
                    is_live INTEGER NOT NULL DEFAULT 0, title TEXT,
                    viewers INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
                    products TEXT, error TEXT, source TEXT,
                    checked_at TEXT DEFAULT (datetime('now'))
                );
                INSERT INTO live_snapshots (room_id, url, is_live, title, source)
                VALUES ('legacy', 'https://live.douyin.com/1', 1, 'legacy', 'browser');
                """
            )
            old_conn.commit()
            old_conn.close()

            db = Database(db_path)
            legacy = db.get_all_live_snapshots(limit=10)
            self.assertEqual(len(legacy), 1)
            self.assertFalse(legacy[0]["degraded"])
            self.assertEqual(legacy[0]["source"], "browser")
            self.assertIsNone(legacy[0]["degrade_reason"])

            db.save_snapshot({
                "room_id": "123456789", "url": ROOM_URL, "is_live": True,
                "viewers": 1234, "source": "http", "degraded": True,
                "degrade_reason": "timeout: 浏览器采集超时",
            })
            snapshots = db.get_recent_snapshots("123456789", limit=10)
            self.assertTrue(snapshots[0]["degraded"])
            self.assertIn("timeout", snapshots[0]["degrade_reason"])
            self.assertEqual(snapshots[0]["source"], "http")

    def test_csv_export_keeps_legacy_columns(self):
        if Database is None:
            self.skipTest(f"lite app 依赖缺失: {_IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as tmpdir:
            # lite/app.py 在导入时会按默认路径创建 data/monitor.db,
            # 因此先切到临时目录再导入, 避免污染仓库。
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                if LITE_DIR not in sys.path:
                    sys.path.insert(0, LITE_DIR)
                try:
                    import app as app_mod
                except Exception as exc:
                    self.skipTest(f"lite app 依赖缺失: {exc}")

                db = Database(os.path.join(tmpdir, "monitor.db"))
                db.save_snapshot({
                    "room_id": "123456789", "url": ROOM_URL, "is_live": True,
                    "title": "标题", "viewers": 1234, "source": "http",
                    "degraded": True, "degrade_reason": "timeout: 浏览器采集超时",
                })
                app_mod.db = db

                response = asyncio.run(app_mod.export_csv())
            finally:
                os.chdir(cwd)
                if LITE_DIR in sys.path:
                    sys.path.remove(LITE_DIR)

            rows = list(csv.reader(io.StringIO(response.body.decode("utf-8"))))
            header = rows[0]
            self.assertEqual(
                header[:10],
                ["时间", "房间ID", "是否直播", "标题", "在线人数",
                 "点赞", "评论", "分享", "商品数", "错误"],
            )
            self.assertEqual(header[10:], ["来源", "是否降级", "降级原因"])
            self.assertEqual(len(rows[1]), len(header))
            self.assertEqual(rows[1][10], "http")
            self.assertEqual(rows[1][11], "是")
            self.assertIn("timeout", rows[1][12])


if __name__ == "__main__":
    unittest.main()
