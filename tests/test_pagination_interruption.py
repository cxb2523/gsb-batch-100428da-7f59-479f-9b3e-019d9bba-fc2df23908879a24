"""翻页中断保留已采集数据、数量校验说明与落盘策略的离线测试。"""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from bs4 import BeautifulSoup

from fc2cmadb_crawler import crawler
from fc2cmadb_crawler.naming import format_film_folder_name


def make_listing_soup(items, total=None, last_page=None):
    payload = {
        "component": "Actresses/Show",
        "props": {
            "articles": {
                "data": items,
                "total": total if total is not None else len(items),
                "last_page": last_page or 1,
            }
        },
    }
    html = (
        '<html><head><script data-page="app" type="application/json">'
        + json.dumps(payload)
        + "</script></head><body></body></html>"
    )
    return BeautifulSoup(html, "html.parser")


def make_item(video_id, title=None, writer="Maker"):
    return {
        "video_id": str(video_id),
        "title": title or f"Title {video_id}",
        "writer": {"name": writer},
    }


def make_film(video_id, title=None, producer="Maker"):
    return {
        "film_number": str(video_id),
        "film_title": title or f"Title {video_id}",
        "producer": producer,
    }


PAGE_ONE_URL = "https://fc2cmadb.com/actresses/10436?page=1"


class PaginationInterruptionTests(unittest.TestCase):
    def test_second_page_load_failure_keeps_first_page_data(self):
        page_one = make_listing_soup([make_item(1001), make_item(1002)], total=4, last_page=2)

        def fetch_page(page):
            self.assertEqual(page, 2)
            return None  # 模拟第二页加载超时

        with redirect_stdout(StringIO()):
            result = crawler.paginate_film_data(fetch_page, total_pages=2, first_page_soup=page_one)

        self.assertEqual([film["film_number"] for film in result.films], ["1001", "1002"])
        self.assertEqual(result.last_completed_page, 1)
        self.assertIsNotNone(result.interruption)
        self.assertEqual(result.interruption.page, 2)
        self.assertEqual(result.interruption.reason, crawler.INTERRUPT_REASON_LOAD_FAILED)

    def test_empty_page_when_more_pages_expected_records_no_film_data(self):
        page_one = make_listing_soup([make_item(1001)], total=3, last_page=2)
        empty_page = make_listing_soup([], total=3, last_page=2)

        with redirect_stdout(StringIO()):
            result = crawler.paginate_film_data(
                lambda page: empty_page, total_pages=2, first_page_soup=page_one
            )

        self.assertEqual([film["film_number"] for film in result.films], ["1001"])
        self.assertIsNotNone(result.interruption)
        self.assertEqual(result.interruption.page, 2)
        self.assertEqual(result.interruption.reason, crawler.INTERRUPT_REASON_NO_FILM_DATA)

    def test_interrupted_result_is_saved_as_partial_with_shortcut(self):
        page_one = make_listing_soup([make_item(1001), make_item(1002)], total=4, last_page=2)
        with redirect_stdout(StringIO()):
            result = crawler.paginate_film_data(
                lambda page: None, total_pages=2, first_page_soup=page_one
            )

        with tempfile.TemporaryDirectory() as tmp:
            actress_folder = Path(tmp) / "Actress A"
            output = StringIO()
            with redirect_stdout(output):
                exit_code = crawler.finalize_actress_results(
                    str(actress_folder),
                    "10436",
                    PAGE_ONE_URL,
                    result.films,
                    result.interruption,
                    4,
                )

            self.assertEqual(exit_code, 1)
            text = output.getvalue()
            self.assertIn("不完整", text)
            self.assertIn("第 2 页", text)
            self.assertTrue(actress_folder.is_dir())
            for film in result.films:
                folder = actress_folder / format_film_folder_name(
                    film["film_number"], film["producer"], film["film_title"]
                )
                self.assertTrue(folder.is_dir(), folder)
            shortcut = actress_folder / "id_10436 - latest_1001.url"
            self.assertTrue(shortcut.is_file())
            self.assertIn(f"URL={PAGE_ONE_URL}", shortcut.read_text())

    def test_full_pagination_count_mismatch_is_reported(self):
        # 完整翻页（只有 1 页，无中断），但网页显示总数与提取数量不符。
        page_one = make_listing_soup([make_item(1001)], total=3, last_page=1)
        with redirect_stdout(StringIO()):
            result = crawler.paginate_film_data(
                lambda page: self.fail("不应继续翻页"), total_pages=1, first_page_soup=page_one
            )
        self.assertIsNone(result.interruption)
        self.assertEqual(len(result.films), 1)

        output = StringIO()
        with redirect_stdout(output):
            valid = crawler.validate_film_count(1, 3, interruption=result.interruption)
        self.assertFalse(valid)
        text = output.getvalue()
        self.assertIn("提取数量", text)
        self.assertIn("网页显示数量", text)
        self.assertIn("差异", text)
        self.assertIn("全部翻页", text)

    def test_full_pagination_mismatch_still_saves_unique_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            actress_folder = Path(tmp) / "Actress A"
            output = StringIO()
            with redirect_stdout(output):
                exit_code = crawler.finalize_actress_results(
                    str(actress_folder),
                    "10436",
                    PAGE_ONE_URL,
                    [make_film(1001), make_film(1002)],
                    None,
                    3,
                )
            self.assertEqual(exit_code, 1)
            text = output.getvalue()
            self.assertIn("全部翻页", text)
            self.assertIn("不完整", text)
            self.assertTrue(actress_folder.is_dir())

    def test_zero_successful_pages_creates_no_directories(self):
        def fetch_page(page):
            self.assertEqual(page, 1)
            return None  # 第一页就加载失败

        with redirect_stdout(StringIO()):
            result = crawler.paginate_film_data(fetch_page, total_pages=3)

        self.assertEqual(result.films, [])
        self.assertEqual(result.last_completed_page, 0)
        self.assertIsNotNone(result.interruption)
        self.assertEqual(result.interruption.page, 1)
        self.assertEqual(result.interruption.reason, crawler.INTERRUPT_REASON_LOAD_FAILED)

        with tempfile.TemporaryDirectory() as tmp:
            actress_folder = Path(tmp) / "Actress A"
            output = StringIO()
            with redirect_stdout(output):
                exit_code = crawler.finalize_actress_results(
                    str(actress_folder),
                    "10436",
                    PAGE_ONE_URL,
                    result.films,
                    result.interruption,
                    5,
                )
            self.assertEqual(exit_code, 1)
            self.assertIn("不创建目录", output.getvalue())
            self.assertFalse(actress_folder.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_duplicate_film_numbers_are_not_saved(self):
        films = [make_film(1001), make_film(1001), make_film(1002)]
        self.assertEqual(crawler.find_duplicate_film_numbers(films), ["1001"])

        with tempfile.TemporaryDirectory() as tmp:
            actress_folder = Path(tmp) / "Actress A"
            output = StringIO()
            with redirect_stdout(output):
                exit_code = crawler.finalize_actress_results(
                    str(actress_folder),
                    "10436",
                    PAGE_ONE_URL,
                    films,
                    crawler.PaginationInterruption(2, crawler.INTERRUPT_REASON_LOAD_FAILED),
                    5,
                )
            self.assertEqual(exit_code, 1)
            self.assertIn("重复影片编号", output.getvalue())
            self.assertFalse(actress_folder.exists())

    def test_successful_crawl_creates_folders_and_returns_zero(self):
        films = [make_film(1001), make_film(1002)]
        with tempfile.TemporaryDirectory() as tmp:
            actress_folder = Path(tmp) / "Actress A"
            output = StringIO()
            with redirect_stdout(output):
                exit_code = crawler.finalize_actress_results(
                    str(actress_folder), "10436", PAGE_ONE_URL, films, None, 2
                )
            self.assertEqual(exit_code, 0)
            self.assertIn("数量校验通过", output.getvalue())
            self.assertTrue((actress_folder / "id_10436 - latest_1001.url").is_file())
            self.assertEqual(
                sorted(p.name for p in actress_folder.iterdir() if p.is_dir()),
                sorted(
                    format_film_folder_name(f["film_number"], f["producer"], f["film_title"])
                    for f in films
                ),
            )


if __name__ == "__main__":
    unittest.main()
