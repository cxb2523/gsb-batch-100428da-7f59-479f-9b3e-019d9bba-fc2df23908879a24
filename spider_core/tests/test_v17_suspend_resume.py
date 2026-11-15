"""test_v17_suspend_resume — 挂起/续跑异常链路测试（零网络，全部使用本地构造 HTML）。

覆盖三条路径：
1. 正常续跑：详情页挂起后从同一位置恢复，不重复抓取已入库影片；
2. 登录页/访问限制挂起：进入 waiting_cookie，断点写入阶段、页码/序号、番号、已抓数量，
   任务日志留下可读说明，接口返回挂起原因与断点位置，恢复后挂起信息清除；
3. 网络或代理错误：标记为 failed 并保留断点，不进入 waiting_cookie。
"""

import gc
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_store  # noqa: E402
import spider_engine  # noqa: E402
from services.task_service import task_to_response  # noqa: E402

LIST_HTML = """
<html><body>
<div class="movie-list">
  <a class="box" href="/v/movie-001" title="MOVIE-001"><div class="video-title"><strong>MOVIE-001</strong></div></a>
  <a class="box" href="/v/movie-002" title="MOVIE-002"><div class="video-title"><strong>MOVIE-002</strong></div></a>
  <a class="box" href="/v/movie-003" title="MOVIE-003"><div class="video-title"><strong>MOVIE-003</strong></div></a>
</div>
</body></html>
"""

LOGIN_HTML = """
<html><body>
<form action="/login" method="post">
  <input type="text" name="username" />
  <input type="password" name="password" />
</form>
</body></html>
"""


def detail_html(code):
    return f"""
    <html><body>
    <div id="magnets-content">
      <div class="item">
        <a href="magnet:?xt=urn:btih:{code.lower()}">下载</a>
        <span class="name">{code}-1080p</span>
        <div class="tags"><span class="tag">高清</span></div>
        <span class="meta">1.0GB</span>
        <span class="date"><span class="time">2026-01-01</span></span>
      </div>
    </div>
    </body></html>
    """


def make_response(html, url, status_code=200):
    return types.SimpleNamespace(text=html, status_code=status_code, url=url)


class SuspendResumeBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_data_dir = os.path.dirname(db_store.get_db_path())
        db_store.configure(self.tmpdir.name)
        self.task_id = db_store.create_task(
            "https://javdb.com/actors/demo",
            filename="demo.csv",
            crawl_mode="overwrite",
        )

    def tearDown(self):
        db_store.configure(self.old_data_dir)
        # Windows 上 SQLite(WAL) 句柄释放有延迟，先 GC 再清理临时目录
        gc.collect()
        try:
            self.tmpdir.cleanup()
        except PermissionError:
            pass

    def run_spider(self, fake_fetch, **overrides):
        kwargs = {
            "start_url": "https://javdb.com/actors/demo",
            "cookie": "session=abc",
            "user_agent": "UA",
            "output_filename": "demo.csv",
            "crawl_mode": "overwrite",
            "task_id": self.task_id,
        }
        kwargs.update(overrides)
        with patch.object(spider_engine, "DATA_DIR", self.tmpdir.name), \
                patch.object(spider_engine, "CHECKPOINT_FILE", os.path.join(self.tmpdir.name, "checkpoint.json")), \
                patch.object(spider_engine, "STATUS_FILE", os.path.join(self.tmpdir.name, "status.json")), \
                patch.object(spider_engine, "fetch_html", side_effect=fake_fetch), \
                patch.object(spider_engine.time, "sleep"):
            spider_engine.run_spider(**kwargs)

    def task(self):
        return db_store.get_task(self.task_id)

    def checkpoint(self):
        return db_store.load_task_checkpoint(self.task_id)

    def collection_codes(self):
        movies = db_store.get_collection_movies("demo.csv")["movies"]
        return [movie["code"] for movie in movies]


class ListPageLoginSuspendTest(SuspendResumeBase):
    def test_login_page_at_list_suspends_with_checkpoint_context(self):
        def fake_fetch(url, headers=None, proxies=None):
            return make_response(LOGIN_HTML, url)

        self.run_spider(fake_fetch)

        # 进入等待 Cookie，而不是失败
        self.assertEqual(self.task()["state"], "waiting_cookie")

        # 断点中写入阶段、页码、已抓数量与原因
        checkpoint = self.checkpoint()
        self.assertEqual(checkpoint["phase"], 1)
        self.assertEqual(checkpoint["page"], 1)
        suspend = checkpoint["suspend"]
        self.assertEqual(suspend["stage"], "list")
        self.assertEqual(suspend["page"], 1)
        self.assertEqual(suspend["fetched_count"], 0)
        self.assertEqual(suspend["position_label"], "目录页第 1 页")
        self.assertIn("登录", suspend["reason"])

        # 任务日志中有一条可读说明
        logs = db_store.get_task_logs(self.task_id)
        self.assertTrue(
            any("断点已保存" in log and "目录页第 1 页" in log for log in logs),
            f"缺少可读断点日志: {logs}",
        )

        # 任务接口返回挂起原因与断点位置
        data = task_to_response(self.task())
        self.assertEqual(data["suspend_stage"], "list")
        self.assertEqual(data["suspend_position"], "目录页第 1 页")
        self.assertIn("登录", data["suspend_reason"])

    def test_server_error_at_list_fails_instead_of_waiting_cookie(self):
        def fake_fetch(url, headers=None, proxies=None):
            return make_response("<html>Server Error</html>", url, status_code=503)

        self.run_spider(fake_fetch)

        self.assertEqual(self.task()["state"], "failed")
        suspend = self.checkpoint()["suspend"]
        self.assertEqual(suspend["stage"], "list")
        self.assertEqual(suspend["reason_type"], "network_error")
        self.assertEqual(self.task()["task_cookie_failure_count"], 0)


class DetailPageSuspendThenResumeTest(SuspendResumeBase):
    def test_detail_login_suspend_then_resume_without_duplicates(self):
        fetched_urls = []

        def fetch_until_login(url, headers=None, proxies=None):
            fetched_urls.append(url)
            if "/v/movie-002" in url:
                return make_response(LOGIN_HTML, url)
            if "/v/" in url:
                code = url.rsplit("/", 1)[-1].split("?")[0].upper()
                return make_response(detail_html(code), url)
            return make_response(LIST_HTML, url)

        # 第一次运行：第 2 部详情页返回登录页 → 挂起等待 Cookie
        self.run_spider(fetch_until_login)

        self.assertEqual(self.task()["state"], "waiting_cookie")
        self.assertEqual(self.collection_codes(), ["MOVIE-001"])

        checkpoint = self.checkpoint()
        self.assertEqual(checkpoint["phase"], 2)
        self.assertEqual(checkpoint["current_index"], 1)
        suspend = checkpoint["suspend"]
        self.assertEqual(suspend["stage"], "detail")
        self.assertEqual(suspend["index"], 1)
        self.assertEqual(suspend["total"], 3)
        self.assertEqual(suspend["code"], "MOVIE-002")
        self.assertEqual(suspend["fetched_count"], 1)
        self.assertEqual(suspend["position_label"], "详情页第 2/3 部")

        logs = db_store.get_task_logs(self.task_id)
        self.assertTrue(
            any("断点已保存" in log and "MOVIE-002" in log and "第 2/3 部" in log for log in logs),
            f"缺少可读断点日志: {logs}",
        )

        data = task_to_response(self.task())
        self.assertEqual(data["suspend_position"], "详情页第 2/3 部")
        self.assertEqual(data["suspend_code"], "MOVIE-002")
        self.assertEqual(data["suspend_fetched_count"], 1)

        # 补 Cookie 恢复：挂起原因与断点位置字段清除，位置数据保留
        self.assertTrue(db_store.resume_task_to_pending(self.task_id))
        resumed = task_to_response(self.task())
        self.assertEqual(resumed["suspend_reason"], "")
        self.assertEqual(resumed["suspend_position"], "")
        checkpoint = self.checkpoint()
        self.assertNotIn("suspend", checkpoint)
        self.assertEqual(checkpoint["current_index"], 1)

        # 第二次运行：从第 2 部继续，不重复抓取已入库的 MOVIE-001
        fetched_urls.clear()

        def fetch_all_ok(url, headers=None, proxies=None):
            fetched_urls.append(url)
            code = url.rsplit("/", 1)[-1].split("?")[0].upper()
            return make_response(detail_html(code), url)

        self.run_spider(fetch_all_ok, is_resume=True)

        self.assertEqual(self.task()["state"], "finished")
        self.assertFalse(any("/v/movie-001" in url for url in fetched_urls))
        self.assertTrue(any("/v/movie-002" in url for url in fetched_urls))
        self.assertTrue(any("/v/movie-003" in url for url in fetched_urls))
        # 已入库影片不重复：3 部且番号唯一
        self.assertEqual(sorted(self.collection_codes()), ["MOVIE-001", "MOVIE-002", "MOVIE-003"])


class NetworkErrorFailureTest(SuspendResumeBase):
    def test_network_error_at_list_marks_failed_and_keeps_checkpoint(self):
        def fake_fetch(url, headers=None, proxies=None):
            raise ConnectionError("connection reset")

        self.run_spider(fake_fetch)

        # 网络或代理错误 → 失败，而不是等待 Cookie
        self.assertEqual(self.task()["state"], "failed")
        self.assertIn("网络或代理", self.task()["error_message"])
        self.assertEqual(self.task()["task_cookie_failure_count"], 0)

        # 断点保留，带阶段与位置信息
        suspend = self.checkpoint()["suspend"]
        self.assertEqual(suspend["stage"], "list")
        self.assertEqual(suspend["page"], 1)
        self.assertEqual(suspend["reason_type"], "network_error")

        logs = db_store.get_task_logs(self.task_id)
        self.assertTrue(any("断点已保留" in log for log in logs), f"缺少断点保留日志: {logs}")

        data = task_to_response(self.task())
        self.assertEqual(data["suspend_position"], "目录页第 1 页")
        self.assertIn("网络或代理", data["suspend_reason"])

    def test_network_error_at_detail_marks_failed_with_movie_position(self):
        def fake_fetch(url, headers=None, proxies=None):
            if "/v/movie-002" in url:
                raise ConnectionError("proxy unreachable")
            if "/v/" in url:
                code = url.rsplit("/", 1)[-1].split("?")[0].upper()
                return make_response(detail_html(code), url)
            return make_response(LIST_HTML, url)

        self.run_spider(fake_fetch)

        self.assertEqual(self.task()["state"], "failed")
        self.assertEqual(self.collection_codes(), ["MOVIE-001"])

        checkpoint = self.checkpoint()
        self.assertEqual(checkpoint["phase"], 2)
        self.assertEqual(checkpoint["current_index"], 1)
        suspend = checkpoint["suspend"]
        self.assertEqual(suspend["stage"], "detail")
        self.assertEqual(suspend["code"], "MOVIE-002")
        self.assertEqual(suspend["position_label"], "详情页第 2/3 部")
        self.assertEqual(suspend["fetched_count"], 1)
        self.assertEqual(suspend["reason_type"], "network_error")

        data = task_to_response(self.task())
        self.assertEqual(data["suspend_stage"], "detail")
        self.assertEqual(data["suspend_position"], "详情页第 2/3 部")


if __name__ == "__main__":
    unittest.main(verbosity=2)
