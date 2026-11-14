import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
from database import CrawlRepository


class NeeqDownloadFlowTest(unittest.TestCase):
    def test_second_run_uses_downloaded_pdf_from_sqlite(self):
        announcement = {
            "secCode": "001234",
            "secName": "测试公司",
            "announcementTitle": "测试公司：2024年年度报告",
            "announcementDate": "2025-04-20",
            "adjunctUrl": "https://example.com/report.pdf",
            "adjunctType": "PDF",
        }
        args = SimpleNamespace(skip_parse=True, year=2025)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dirs = {
                "pdf": str(root / "pdf"),
                "csv": str(root / "csv"),
                "log": str(root / "log"),
            }
            for directory in output_dirs.values():
                Path(directory).mkdir(parents=True)

            def fake_download(_url, save_path, error_info=None):
                Path(save_path).write_bytes(b"%PDF-1.7\ncontent")
                return True

            with patch.dict(main.OUTPUT_DIR, output_dirs):
                with patch.object(
                    main,
                    "STOP_FILE",
                    str(root / "STOP.txt"),
                ):
                    with patch(
                        "neeq_crawler.download_pdf",
                        side_effect=fake_download,
                    ) as download_mock:
                        with CrawlRepository(
                            root / "crawl_state.db"
                        ) as repository:
                            repository.upsert_announcements(
                                [announcement],
                                source="neeq",
                            )
                            first_stats = main._process_neeq_announcements(
                                [announcement],
                                args,
                                main.logging.getLogger("test"),
                                repository,
                            )
                            second_stats = main._process_neeq_announcements(
                                [announcement],
                                args,
                                main.logging.getLogger("test"),
                                repository,
                            )
                            record = repository.get_announcement(
                                "neeq",
                                announcement["adjunctUrl"],
                            )

            self.assertEqual(download_mock.call_count, 1)
            self.assertEqual(first_stats["downloaded"], 1)
            self.assertEqual(second_stats["existing"], 1)
            self.assertEqual(record["status"], "downloaded")
            self.assertEqual(record["attempts"], 1)
            self.assertGreater(record["file_size"], 0)

    @staticmethod
    def _make_announcement(url):
        return {
            "secCode": "001234",
            "secName": "测试公司",
            "announcementTitle": "测试公司：2024年年度报告",
            "announcementDate": "2025-04-20",
            "adjunctUrl": url,
            "adjunctType": "PDF",
        }

    def _run_process(self, announcements, repository, temp_root):
        args = SimpleNamespace(skip_parse=True, year=2025)
        output_dirs = {
            "pdf": str(temp_root / "pdf"),
            "csv": str(temp_root / "csv"),
            "log": str(temp_root / "log"),
        }
        for directory in output_dirs.values():
            Path(directory).mkdir(parents=True, exist_ok=True)
        with patch.dict(main.OUTPUT_DIR, output_dirs):
            with patch.object(main, "STOP_FILE", str(temp_root / "STOP.txt")):
                return main._process_neeq_announcements(
                    announcements,
                    args,
                    main.logging.getLogger("test"),
                    repository,
                )

    def test_failed_download_records_category_error_and_attempts(self):
        announcement = self._make_announcement("https://example.com/report.pdf")

        def fake_download(_url, _save_path, error_info=None):
            if error_info is not None:
                error_info["category"] = "not_pdf"
                error_info["message"] = "响应内容不是有效的 PDF 文件"
            return False

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "neeq_crawler.download_pdf",
                side_effect=fake_download,
            ):
                with CrawlRepository(root / "crawl_state.db") as repository:
                    repository.upsert_announcements([announcement], source="neeq")
                    stats = self._run_process([announcement], repository, root)
                    record = repository.get_announcement(
                        "neeq",
                        announcement["adjunctUrl"],
                    )

            self.assertEqual(stats["failed"], 1)
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["attempts"], 1)
            self.assertIn("[not_pdf]", record["last_error"])
            self.assertIn("不是有效的 PDF", record["last_error"])
            self.assertFalse(list(root.rglob("*.part")))

    def test_temporary_failure_is_distinguishable_from_not_pdf(self):
        announcement = self._make_announcement("https://example.com/report.pdf")

        def fake_download(_url, _save_path, error_info=None):
            if error_info is not None:
                error_info["category"] = "network"
                error_info["message"] = "连接超时"
            return False

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "neeq_crawler.download_pdf",
                side_effect=fake_download,
            ):
                with CrawlRepository(root / "crawl_state.db") as repository:
                    repository.upsert_announcements([announcement], source="neeq")
                    stats = self._run_process([announcement], repository, root)
                    record = repository.get_announcement(
                        "neeq",
                        announcement["adjunctUrl"],
                    )

            self.assertEqual(stats["failed"], 1)
            self.assertEqual(record["status"], "failed")
            self.assertIn("[network]", record["last_error"])

    def test_downloaded_record_is_not_overwritten_by_failure(self):
        announcement = self._make_announcement("https://example.com/report.pdf")

        def fake_download(_url, _save_path, error_info=None):
            if error_info is not None:
                error_info["category"] = "network"
                error_info["message"] = "连接超时"
            return False

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_file = root / "pdf" / "gone.pdf"
            with patch(
                "neeq_crawler.download_pdf",
                side_effect=fake_download,
            ) as download_mock:
                with CrawlRepository(root / "crawl_state.db") as repository:
                    repository.upsert_announcements([announcement], source="neeq")
                    repository.mark_downloaded(
                        "neeq",
                        announcement["adjunctUrl"],
                        file_path=missing_file,
                        file_size=1024,
                    )
                    # 记录已 downloaded 但本地文件缺失：不得降级为 failed
                    stats = self._run_process([announcement], repository, root)
                    record = repository.get_announcement(
                        "neeq",
                        announcement["adjunctUrl"],
                    )

            self.assertEqual(download_mock.call_count, 0)
            self.assertEqual(stats["failed"], 0)
            self.assertEqual(record["status"], "downloaded")
            self.assertIsNone(record["last_error"])


if __name__ == "__main__":
    unittest.main()
