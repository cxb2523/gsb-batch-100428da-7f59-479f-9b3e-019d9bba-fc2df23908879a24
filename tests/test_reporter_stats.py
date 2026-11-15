import logging
import tempfile
import unittest
from pathlib import Path

from utils.reporter import ReportGenerator

RESULTS = {
    "scan_info": {"urls": ["https://t.example"], "urls_count": 1,
                  "start_time": "2026-08-30T10:00:00",
                  "end_time": "2026-08-30T10:05:00", "duration": "0:05:00"},
    "statistics": {"js_files_found": 9, "secrets_found": 1, "endpoints_found": 29},
    "findings": {
        "secrets": [{"type": "aws_api_key", "value": "AKIAIOSFODNN7EXAMPLE",
                     "file": "a.js", "context": "x", "confidence": "high",
                     "entropy": 4.0, "category": "cloud", "source": "pattern_match"}],
        "endpoints": ([{"url": f"/api/{i}", "file": "a.js"} for i in range(26)]
                      + [{"url": "https://api.example.com/v1", "file": "a.js"},
                         {"url": "https://cdn.example.net/x", "file": "a.js"},
                         {"url": "https://api.example.com/v2", "file": "a.js"}]),
        "js_files": [{"filepath": f"{i}.js", "filename": f"{i}.js",
                      "url": "u", "size": 1} for i in range(9)]},
}


class TestMarkdownStatistics(unittest.TestCase):
    def _markdown(self):
        with tempfile.TemporaryDirectory() as d:
            ReportGenerator(output_dir=d, logger=logging.getLogger("t")) \
                .generate_report(RESULTS, "md")
            return (Path(d) / "reports" / "report.md").read_text(encoding="utf-8")

    def test_reports_actual_js_file_count(self):
        self.assertIn("**JavaScript Files:** 9", self._markdown())

    def test_reports_actual_endpoint_count(self):
        self.assertIn("**Endpoints Discovered:** 29", self._markdown())

    def test_reports_actual_secret_count(self):
        self.assertIn("**Secrets Found:** 1", self._markdown())

    def test_unique_domains_derived_from_endpoints(self):
        """Never written by the pipeline - must be derived from absolute URLs.

        Two distinct hosts across three absolute endpoints; relative paths
        contribute nothing.
        """
        self.assertIn("**Unique Domains:** 2", self._markdown())


if __name__ == "__main__":
    unittest.main()
