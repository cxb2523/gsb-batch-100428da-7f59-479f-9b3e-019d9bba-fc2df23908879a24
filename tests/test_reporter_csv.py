import csv
import logging
import tempfile
import unittest
from pathlib import Path

from utils.reporter import ReportGenerator

EMPTY = {
    "scan_info": {"urls": ["https://t.example"], "urls_count": 1,
                  "start_time": "2026-08-30T10:00:00",
                  "end_time": "2026-08-30T10:05:00", "duration": "0:05:00"},
    "statistics": {"js_files_found": 0, "secrets_found": 0, "endpoints_found": 0},
    "findings": {"secrets": [], "endpoints": [], "js_files": []},
}

POPULATED = {
    "scan_info": EMPTY["scan_info"],
    "statistics": {"js_files_found": 1, "secrets_found": 1, "endpoints_found": 1},
    "findings": {
        "secrets": [{"type": "aws_api_key", "value": "AKIAIOSFODNN7EXAMPLE",
                     "file": "a.js", "context": "x", "confidence": "high",
                     "entropy": 4.0}],
        "endpoints": [{"url": "/api/v1", "source_file": "a.js"}],
        "js_files": [{"filename": "a.js", "url": "https://t.example/a.js",
                      "size": 12, "sha256": "abc", "source_page": "https://t.example"}],
    },
}

CSV_FILES = ("secrets.csv", "endpoints.csv", "js_files.csv")


def _run_csv(results):
    d = tempfile.mkdtemp()
    ReportGenerator(output_dir=d, logger=logging.getLogger("t")) \
        .generate_report(results, "csv")
    return Path(d) / "reports"


class TestCsvOnZeroFindings(unittest.TestCase):
    """A clean scan is a real result, not a crash.

    Anything scripting around jsrip reads these paths unconditionally; a
    missing file is indistinguishable from a failed run.
    """

    @classmethod
    def setUpClass(cls):
        cls.reports = _run_csv(EMPTY)

    def test_every_csv_is_written(self):
        for name in CSV_FILES:
            with self.subTest(file=name):
                self.assertTrue((self.reports / name).exists(),
                                f"{name} missing after a zero-finding scan")

    def test_every_csv_carries_its_header_row(self):
        expected = {
            "secrets.csv": ["Type", "Value", "File", "Confidence", "Entropy", "Context"],
            "endpoints.csv": ["URL", "Source File"],
            "js_files.csv": ["Filename", "URL", "Size", "SHA256", "Source Page"],
        }
        for name, header in expected.items():
            with self.subTest(file=name):
                rows = list(csv.reader((self.reports / name).open(encoding="utf-8")))
                self.assertEqual(rows[0], header)

    def test_no_data_rows_beyond_the_header(self):
        for name in CSV_FILES:
            with self.subTest(file=name):
                rows = list(csv.reader((self.reports / name).open(encoding="utf-8")))
                self.assertEqual(len(rows), 1)


class TestCsvWithFindings(unittest.TestCase):
    """Guards the fix above from being an over-correction that drops rows."""

    @classmethod
    def setUpClass(cls):
        cls.reports = _run_csv(POPULATED)

    def test_findings_still_reach_their_rows(self):
        expected = {
            "secrets.csv": "AKIAIOSFODNN7EXAMPLE",
            "endpoints.csv": "/api/v1",
            "js_files.csv": "a.js",
        }
        for name, value in expected.items():
            with self.subTest(file=name):
                rows = list(csv.reader((self.reports / name).open(encoding="utf-8")))
                self.assertEqual(len(rows), 2)
                self.assertIn(value, rows[1])


if __name__ == "__main__":
    unittest.main()
