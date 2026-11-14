"""Partial-export behaviour for the BOSS直聘 crawler.

These tests never launch a browser and never touch the network: the browser,
page and pagination layer are replaced with in-memory fakes so that the
pagination/export loop can be driven deterministically.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

import p
from p import (
    PARTIAL_EXPORT_EXIT_CODE,
    BossZhipinSpider,
    CrawlerError,
    SecurityVerificationRequired,
    cli,
)


JOB_HTML = """
<main>
  <ul class="job-list-box">
    <li class="job-card-wrapper">
      <span class="job-name">测试工程师</span>
      <div class="job-info clearfix">
        <span>10-15K</span>
        <ul><li>1-3年</li><li>本科</li></ul>
      </div>
      <div class="company-info"><h3><a>示例科技</a></h3></div>
      <span class="job-area">福州·鼓楼区</span>
      <div class="info-desc">双休，五险一金</div>
      <div class="job-card-footer clearfix"><ul class="tag-list"><li>Python</li></ul></div>
      <ul class="company-tag-list"><li>互联网</li><li>100-499人</li></ul>
    </li>
    <li class="company-job-item">
      <a class="job-info">
        <div class="job-info-top">
          <p class="name">数据分析师</p>
          <p class="salary">15-25K</p>
        </div>
        <p class="job-text"><span>福州·仓山区</span><span>经验不限</span><span>大专</span></p>
        <p class="company-name">另一家科技</p>
      </a>
    </li>
  </ul>
</main>
"""

EMPTY_HTML = "<main><div>no jobs here</div></main>"


class FakePage:
    """Stand-in for a pyppeteer Page; only ``content`` is used by main()."""

    def __init__(self, contents):
        self._contents = list(contents)
        self.content_calls = 0

    async def content(self):
        index = min(self.content_calls, len(self._contents) - 1)
        self.content_calls += 1
        return self._contents[index]


class FakeBrowser:
    """Stand-in for a pyppeteer Browser; records that close() was awaited."""

    def __init__(self, page):
        self._page = page
        self.closed = False

    async def newPage(self):
        return self._page

    async def close(self):
        self.closed = True


def _chained_crawler_error(message, cause_message):
    """Build a CrawlerError the way _next_page does, with an original cause."""
    error = CrawlerError(message)
    error.__cause__ = TimeoutError(cause_message)
    return error


def run_cli(
    output,
    *,
    contents,
    next_page_side_effect,
    wait_side_effect=None,
    max_pages=3,
):
    """Run cli() against fakes and capture stdout/stderr plus the spider."""
    page = FakePage(contents)
    browser = FakeBrowser(page)
    argv = ["--output", str(output), "--max-pages", str(max_pages)]

    created = []
    real_init = BossZhipinSpider.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        created.append(self)

    patches = [
        # A real pyppeteer launch must never happen during these tests.
        mock.patch.object(p, "launch", side_effect=AssertionError("browser launched")),
        mock.patch.object(
            BossZhipinSpider, "__init__", autospec=True, side_effect=tracking_init
        ),
        mock.patch.object(
            BossZhipinSpider,
            "_launch_browser",
            new=mock.AsyncMock(return_value=browser),
        ),
        mock.patch.object(BossZhipinSpider, "_prepare_page", new=mock.AsyncMock()),
        mock.patch.object(BossZhipinSpider, "_search", new=mock.AsyncMock()),
        mock.patch.object(
            BossZhipinSpider,
            "_wait_for_selector",
            new=mock.AsyncMock(side_effect=wait_side_effect),
        ),
        mock.patch.object(
            BossZhipinSpider,
            "_next_page",
            new=mock.AsyncMock(side_effect=next_page_side_effect),
        ),
    ]

    stderr, stdout = io.StringIO(), io.StringIO()
    with redirect_stderr(stderr), redirect_stdout(stdout):
        for patch in patches:
            patch.start()
        try:
            code = cli(argv)
        finally:
            for patch in patches:
                patch.stop()

    return {
        "code": code,
        "stderr": stderr.getvalue(),
        "stdout": stdout.getvalue(),
        "browser": browser,
        "spider": created[-1],
    }


def workbook_row_count(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        # Including the header row.
        return len(list(workbook.active.values))
    finally:
        workbook.close()


class PartialExportTests(unittest.TestCase):
    def test_page_two_turn_failure_keeps_page_one_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "partial.xlsx"
            turn_error = _chained_crawler_error(
                "点击下一页失败（选择器: a.next）",
                "Navigation Timeout Exceeded: 30000 ms exceeded",
            )

            result = run_cli(
                output,
                contents=[JOB_HTML, JOB_HTML],
                # Page 1 parses fine; turning onto page 2 blows up.
                next_page_side_effect=[turn_error],
                max_pages=3,
            )

            self.assertEqual(result["code"], PARTIAL_EXPORT_EXIT_CODE)
            # 2 is reserved for argument validation; never reuse it here.
            self.assertNotEqual(result["code"], 2)
            self.assertTrue(output.exists(), "已采集数据必须照常写入工作簿")
            # Header + the two cards parsed from page 1.
            self.assertEqual(workbook_row_count(output), 3)
            self.assertIn("第 2 页", result["stderr"])
            self.assertIn("Navigation Timeout Exceeded", result["stderr"])
            self.assertIn("点击下一页失败", result["stderr"])
            self.assertIsNotNone(result["spider"].stop_reason)
            self.assertTrue(result["browser"].closed)

    def test_first_screen_without_data_fails_without_workbook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "empty.xlsx"

            result = run_cli(
                output,
                contents=[EMPTY_HTML],
                # No further page either: the run must fail on empty data.
                next_page_side_effect=[False],
                max_pages=3,
            )

            self.assertEqual(result["code"], 1)
            self.assertFalse(output.exists(), "无数据时不得产出空工作簿")
            self.assertIn("抓取失败", result["stderr"])
            self.assertIsNone(result["spider"].stop_reason)

    def test_page_three_wait_timeout_keeps_earlier_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "partial_wait.xlsx"
            wait_error = CrawlerError("等待职位列表超时；页面可能已改版或需要安全验证。")

            result = run_cli(
                output,
                contents=[JOB_HTML, JOB_HTML, JOB_HTML],
                # Page 1 -> 2 turn succeeds; page 2 -> 3 turn also succeeds;
                # then waiting for page 3's cards times out.
                next_page_side_effect=[True, True],
                wait_side_effect=[None, None, wait_error],
                max_pages=3,
            )

            self.assertEqual(result["code"], PARTIAL_EXPORT_EXIT_CODE)
            self.assertTrue(output.exists())
            # Two fully parsed pages (2 cards each) + header.
            self.assertEqual(workbook_row_count(output), 5)
            self.assertIn("第 3 页", result["stderr"])
            self.assertIn("等待职位列表超时", result["stderr"])
            self.assertIsNotNone(result["spider"].stop_reason)

    def test_first_screen_wait_failure_fails_without_workbook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "none.xlsx"

            result = run_cli(
                output,
                contents=[JOB_HTML],
                next_page_side_effect=[AssertionError("不应尝试翻页")],
                wait_side_effect=CrawlerError("等待职位列表超时；页面可能已改版。"),
                max_pages=3,
            )

            self.assertEqual(result["code"], 1)
            self.assertFalse(output.exists())
            self.assertIn("抓取失败", result["stderr"])
            self.assertIn("等待职位列表超时", result["stderr"])

    def test_verification_on_page_two_saves_data_and_reports_stop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "verify.xlsx"
            verification_error = SecurityVerificationRequired(
                "BOSS直聘要求完成安全验证。请使用 --headful --wait-for-verification，"
                "本脚本不会自动绕过验证。"
            )

            result = run_cli(
                output,
                contents=[JOB_HTML, JOB_HTML],
                next_page_side_effect=[verification_error],
                max_pages=3,
            )

            self.assertEqual(result["code"], PARTIAL_EXPORT_EXIT_CODE)
            self.assertTrue(output.exists())
            self.assertEqual(workbook_row_count(output), 3)
            self.assertIn("第 2 页", result["stderr"])
            self.assertIn("安全验证", result["stderr"])
            # SecurityVerificationRequired semantics are preserved, not bypassed.
            self.assertIsInstance(verification_error, SecurityVerificationRequired)
            self.assertIn("安全验证", result["spider"].stop_reason)

    def test_full_run_keeps_success_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "full.xlsx"

            result = run_cli(
                output,
                contents=[JOB_HTML, JOB_HTML],
                # Page 1 -> page 2 succeeds; page 2 has no next button.
                next_page_side_effect=[True, False],
                max_pages=3,
            )

            self.assertEqual(result["code"], 0)
            self.assertTrue(output.exists())
            # Header + two cards per page.
            self.assertEqual(workbook_row_count(output), 5)
            self.assertNotIn("警告", result["stderr"])
            self.assertIsNone(result["spider"].stop_reason)
            self.assertIn("已写入 4 条数据", result["stdout"])


if __name__ == "__main__":
    unittest.main()
