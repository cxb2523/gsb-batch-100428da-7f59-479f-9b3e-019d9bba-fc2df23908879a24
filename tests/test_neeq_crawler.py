import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from neeq_crawler import (
    _normalize_announcement,
    _parse_jsonp,
    classify_download_error,
    download_pdf,
    is_annual_report_title,
    is_valid_pdf,
)


class NeeqCrawlerTest(unittest.TestCase):
    @staticmethod
    def source_item(**overrides):
        item = {
            "companyCd": "000001",
            "companyName": "测试公司",
            "disclosureTitle": "测试公司：2024年年度报告",
            "disclosurePostTitle": "",
            "destFilePath": "/disclosure/2025/report.PDF",
            "publishDate": "2025-04-20",
            "fileExt": "PDF",
        }
        item.update(overrides)
        return item

    def test_parses_neeq_jsonp_response(self):
        parsed = _parse_jsonp(
            'null([{"listInfo":{"content":[],"totalPages":0}}])'
        )
        self.assertEqual(parsed[0]["listInfo"]["totalPages"], 0)

    def test_normalizes_valid_annual_report(self):
        result = _normalize_announcement(
            self.source_item(),
            "2025-04-01",
            "2025-04-30",
        )

        self.assertEqual(result["secCode"], "000001")
        self.assertEqual(result["announcementDate"], "2025-04-20")
        self.assertEqual(
            result["adjunctUrl"],
            "https://www.neeq.com.cn/disclosure/2025/report.PDF",
        )

    def test_filters_summaries_cancelled_and_out_of_range_records(self):
        summary = self.source_item(
            disclosureTitle="测试公司：2024年年度报告摘要"
        )
        cancelled = self.source_item(disclosurePostTitle="（已取消）")
        half_year = self.source_item(
            disclosureTitle="测试公司：2026年半年度报告"
        )
        out_of_range = self.source_item(publishDate="2025-05-01")

        for item in (summary, cancelled, half_year, out_of_range):
            with self.subTest(item=item):
                self.assertIsNone(
                    _normalize_announcement(
                        item,
                        "2025-04-01",
                        "2025-04-30",
                    )
                )

    def test_annual_report_title_rejects_interim_and_cancelled(self):
        self.assertTrue(is_annual_report_title("测试公司：2025年年度报告"))
        self.assertFalse(is_annual_report_title("测试公司：2026年半年度报告"))
        self.assertFalse(is_annual_report_title("测试公司：2025年年度报告（已取消）"))
        self.assertFalse(is_annual_report_title("测试公司：2025年年度报告摘要"))
        self.assertFalse(is_annual_report_title("测试公司：2025年第三季度报告"))

    def test_pdf_validation_uses_file_header(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            valid_pdf = Path(temp_dir) / "valid.pdf"
            invalid_pdf = Path(temp_dir) / "invalid.pdf"
            valid_pdf.write_bytes(b"%PDF-1.7\ncontent")
            invalid_pdf.write_bytes(b"<html>error</html>")

            self.assertTrue(is_valid_pdf(valid_pdf))
            self.assertFalse(is_valid_pdf(invalid_pdf))
            self.assertFalse(is_valid_pdf(Path(temp_dir) / "missing.pdf"))


class _FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def iter_content(self, chunk_size=64 * 1024):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    """伪造的 session：依次返回预设响应或抛出预设异常，记录调用次数。"""

    def __init__(self, responses=None, exc=None):
        self.responses = list(responses or [])
        self.exc = exc
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        index = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[index]


class NeeqDownloadTest(unittest.TestCase):
    def _run_download(self, session, save_path, error_info):
        with patch("neeq_crawler.requests.get", new=session.get):
            with patch("neeq_crawler.time.sleep"):
                return download_pdf(
                    "https://www.neeq.com.cn/disclosure/report.pdf",
                    str(save_path),
                    error_info=error_info,
                )

    def test_retries_exhausted_returns_category_and_leaves_no_files(self):
        session = _FakeSession(exc=requests.ConnectionError("连接超时"))
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "report.pdf"
            error_info = {}

            ok = self._run_download(session, save_path, error_info)

            self.assertFalse(ok)
            self.assertEqual(session.calls, 3)
            self.assertEqual(error_info["category"], "network")
            self.assertIn("连接超时", error_info["message"])
            self.assertFalse(save_path.exists())
            self.assertFalse(list(Path(temp_dir).glob("*.part")))

    def test_http_error_is_classified_as_http(self):
        session = _FakeSession(
            responses=[_FakeResponse(b"", status_code=503)],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "report.pdf"
            error_info = {}

            ok = self._run_download(session, save_path, error_info)

            self.assertFalse(ok)
            self.assertEqual(error_info["category"], "http")
            self.assertIn("503", error_info["message"])
            self.assertFalse(save_path.exists())
            self.assertFalse(list(Path(temp_dir).glob("*.part")))

    def test_html_error_page_is_classified_as_not_pdf(self):
        session = _FakeSession(
            responses=[_FakeResponse(b"<html>error</html>")],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "report.pdf"
            error_info = {}

            ok = self._run_download(session, save_path, error_info)

            self.assertFalse(ok)
            self.assertEqual(error_info["category"], "not_pdf")
            self.assertIn("PDF", error_info["message"])
            self.assertFalse(save_path.exists())
            self.assertFalse(list(Path(temp_dir).glob("*.part")))

    def test_local_write_failure_is_classified_as_local_io(self):
        session = _FakeSession(
            responses=[_FakeResponse(b"%PDF-1.7\ncontent")],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "report.pdf"
            error_info = {}
            with patch(
                "neeq_crawler.Path.open",
                side_effect=OSError("磁盘已满"),
            ):
                ok = self._run_download(session, save_path, error_info)

            self.assertFalse(ok)
            self.assertEqual(error_info["category"], "local_io")
            self.assertFalse(save_path.exists())
            self.assertFalse(list(Path(temp_dir).glob("*.part")))

    def test_successful_download_then_second_call_makes_no_request(self):
        session = _FakeSession(
            responses=[_FakeResponse(b"%PDF-1.7\ncontent")],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "report.pdf"
            error_info = {}

            self.assertTrue(self._run_download(session, save_path, error_info))
            self.assertTrue(save_path.read_bytes().startswith(b"%PDF-"))
            self.assertEqual(error_info, {})

            # 已存在合法 PDF：第二次调用直接成功且不再发起请求
            self.assertTrue(self._run_download(session, save_path, error_info))
            self.assertEqual(session.calls, 1)

    def test_classify_download_error_maps_common_exceptions(self):
        self.assertEqual(
            classify_download_error(requests.HTTPError("500")),
            "http",
        )
        self.assertEqual(
            classify_download_error(requests.Timeout("timeout")),
            "network",
        )
        self.assertEqual(
            classify_download_error(ValueError("not a pdf")),
            "not_pdf",
        )
        self.assertEqual(
            classify_download_error(OSError("disk full")),
            "local_io",
        )


if __name__ == "__main__":
    unittest.main()
