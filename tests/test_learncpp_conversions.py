"""Offline tests for the learncpp spider's PDF conversion bookkeeping.

Nothing here touches the network: HTML files are fakes written into a
temporary directory and pdfkit is either stubbed or run against a machine
where wkhtmltopdf is unavailable, so failure paths are exercised
deterministically.
"""

import logging
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import scrapy

from scraper.spiders.learncpp import ConversionResult, LearncppSpider

BENIGN_WKHTMLTOPDF_ERROR = (
    "QNetworkReplyImplPrivate::error: Internal problem, "
    "this method must only be called once."
)


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class LearncppSpiderConversionTestCase(unittest.TestCase):
    def setUp(self):
        self.spider = LearncppSpider()
        # Use an isolated temp directory as the spider's output directory.
        self._tmpdir = tempfile.TemporaryDirectory()
        self.spider.name = self._tmpdir.name

        self.log_handler = _ListHandler()
        self.log_handler.setLevel(logging.DEBUG)
        # Scrapy wraps the stdlib logger in a SpiderLoggerAdapter.
        self.underlying_logger = self.spider.logger.logger
        self.underlying_logger.addHandler(self.log_handler)
        self.underlying_logger.setLevel(logging.DEBUG)

    def tearDown(self):
        # Make sure no worker threads leak out of a test.
        self.spider.shutdown_conversions()
        self.underlying_logger.removeHandler(self.log_handler)
        self._tmpdir.cleanup()

    def _write_html(self, filename, body="<html><body>lesson</body></html>"):
        path = pathlib.Path(self._tmpdir.name) / filename
        path.write_text(body, encoding="utf-8")
        return path

    def test_convert_to_pdf_success_returns_success_result(self):
        self._write_html("1-intro.html")
        with mock.patch(
            "scraper.spiders.learncpp.pdfkit.from_file"
        ) as from_file:
            result = self.spider.convert_to_pdf("1-intro.html")

            self.assertIsInstance(result, ConversionResult)
            self.assertEqual(result.filename, "1-intro.html")
            self.assertTrue(result.success)
            self.assertIsNone(result.error)
            from_file.assert_called_once()

            # The success message is emitted by the worker wrapper, and only
            # when the conversion actually succeeded.
            worker_result = self.spider._run_conversion("1-intro.html")
            self.assertTrue(worker_result.success)

        self.assertTrue(
            any(
                "Converted 1-intro.html to PDF" in message
                for message in self.log_handler.messages
            )
        )

    def test_convert_to_pdf_failure_returns_filename_and_reason(self):
        # Simulates an unavailable conversion command (e.g. wkhtmltopdf
        # missing), which pdfkit raises as OSError.
        self._write_html("2-broken.html")
        failure = OSError('No wkhtmltopdf executable found: "b\'\'"')
        with mock.patch(
            "scraper.spiders.learncpp.pdfkit.from_file",
            side_effect=failure,
        ):
            result = self.spider.convert_to_pdf("2-broken.html")

        self.assertFalse(result.success)
        self.assertEqual(result.filename, "2-broken.html")
        self.assertIn("OSError", result.error)
        self.assertIn("wkhtmltopdf", result.error)
        # The unconditional "Converted ..." message must not appear.
        self.assertFalse(
            any(
                "Converted 2-broken.html to PDF" in message
                for message in self.log_handler.messages
            )
        )

    def test_unexpected_exception_type_is_still_reported_as_failure(self):
        self._write_html("3-weird.html")
        with mock.patch(
            "scraper.spiders.learncpp.pdfkit.from_file",
            side_effect=ValueError("boom"),
        ):
            result = self.spider.convert_to_pdf("3-weird.html")

        self.assertFalse(result.success)
        self.assertIn("ValueError", result.error)
        self.assertIn("boom", result.error)

    def test_benign_qnetwork_error_keeps_original_suppression(self):
        self._write_html("4-benign.html")
        with mock.patch(
            "scraper.spiders.learncpp.pdfkit.from_file",
            side_effect=OSError(BENIGN_WKHTMLTOPDF_ERROR),
        ):
            result = self.spider.convert_to_pdf("4-benign.html")

        self.assertTrue(result.success)
        self.assertIsNone(result.error)

    def test_worker_never_lets_an_exception_escape_the_future(self):
        with mock.patch.object(
            self.spider,
            "convert_to_pdf",
            side_effect=RuntimeError("worker exploded"),
        ):
            future = self.spider.executor.submit(
                self.spider._run_conversion, "5-explodes.html"
            )
            result = future.result()  # must not raise

        self.assertFalse(result.success)
        self.assertEqual(result.filename, "5-explodes.html")
        self.assertIn("RuntimeError", result.error)

    def test_one_failing_page_does_not_stop_the_other_pages(self):
        def fake_from_file(html_path, pdf_path, options=None):
            if "bad" in html_path:
                raise OSError('No wkhtmltopdf executable found: "b\'\'"')
            pathlib.Path(pdf_path).touch()

        for filename in ("6-good.html", "7-bad.html", "8-good.html"):
            self._write_html(filename)

        with mock.patch(
            "scraper.spiders.learncpp.pdfkit.from_file",
            side_effect=fake_from_file,
        ):
            for filename in ("6-good.html", "7-bad.html", "8-good.html"):
                self.spider.submit_conversion(filename)
            total, succeeded, failures = self.spider.shutdown_conversions()

        self.assertEqual(total, 3)
        self.assertEqual(succeeded, 2)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].filename, "7-bad.html")
        self.assertIn("OSError", failures[0].error)

        # The two healthy pages really produced PDF files.
        self.assertTrue(
            (pathlib.Path(self._tmpdir.name) / "6-good.pdf").exists()
        )
        self.assertTrue(
            (pathlib.Path(self._tmpdir.name) / "8-good.pdf").exists()
        )
        self.assertFalse(
            (pathlib.Path(self._tmpdir.name) / "7-bad.pdf").exists()
        )

    def test_real_unavailable_converter_is_recorded_end_to_end(self):
        # The validation suggested by the task: a real local HTML file and a
        # conversion command that cannot run. Skip if wkhtmltopdf happens to
        # be installed on the machine.
        if shutil.which("wkhtmltopdf") is not None:
            self.skipTest("wkhtmltopdf is installed; failure path unavailable")

        self._write_html("9-nocommand.html")
        self.spider.submit_conversion("9-nocommand.html")
        total, succeeded, failures = self.spider.shutdown_conversions()

        self.assertEqual(total, 1)
        self.assertEqual(succeeded, 0)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].filename, "9-nocommand.html")
        self.assertIn("wkhtmltopdf", failures[0].error)
        self.assertFalse(
            (pathlib.Path(self._tmpdir.name) / "9-nocommand.pdf").exists()
        )

    def test_submission_after_shutdown_is_counted_not_raised(self):
        self._write_html("10-late.html")
        self.spider.shutdown_conversions()

        # Must not raise RuntimeError from the shut-down executor.
        self.spider.submit_conversion("10-late.html")
        total, succeeded, failures = self.spider.shutdown_conversions()

        self.assertEqual(total, 1)
        self.assertEqual(succeeded, 0)
        self.assertEqual([f.filename for f in failures], ["10-late.html"])
        self.assertIn("shut down", failures[0].error)

    def test_shutdown_is_idempotent_and_keeps_consistent_counts(self):
        self._write_html("11-once.html")
        with mock.patch("scraper.spiders.learncpp.pdfkit.from_file"):
            self.spider.submit_conversion("11-once.html")
            first = self.spider.shutdown_conversions()
            second = self.spider.shutdown_conversions()

        self.assertEqual(first, second)
        self.assertEqual(second[0], 1)
        self.assertEqual(second[1], 1)
        self.assertEqual(second[2], [])

    def test_summary_log_lists_total_success_failure_and_filenames(self):
        self._write_html("12-ok.html")
        self._write_html("13-nope.html")

        def fake_from_file(html_path, pdf_path, options=None):
            if "13-nope" in html_path:
                raise OSError("converter unavailable")
            pathlib.Path(pdf_path).touch()

        with mock.patch(
            "scraper.spiders.learncpp.pdfkit.from_file",
            side_effect=fake_from_file,
        ):
            self.spider.submit_conversion("12-ok.html")
            self.spider.submit_conversion("13-nope.html")
            self.spider.shutdown_conversions()

        joined = "\n".join(self.log_handler.messages)
        self.assertIn("PDF conversion summary:", joined)
        self.assertIn("Total pages: 2", joined)
        self.assertIn("Succeeded:   1", joined)
        self.assertIn("Failed:      1", joined)
        self.assertIn("13-nope.html", joined)
        self.assertIn("converter unavailable", joined)
        # The failed page must never claim success.
        self.assertNotIn("Converted 13-nope.html to PDF", joined)

    def test_parse_keeps_html_save_and_clean_flow_and_submits_conversion(
        self,
    ):
        body = (
            b"<html><body>"
            b'<div id="comments">goodbye</div>'
            b"<p>lesson content</p>"
            b"</body></html>"
        )
        response = scrapy.http.HtmlResponse(
            url="http://www.learncpp.com/cpp-tutorial/introduction/",
            body=body,
            encoding="utf-8",
        )

        with mock.patch.object(
            self.spider, "submit_conversion"
        ) as submit_conversion:
            self.spider.parse(response, page_index=14)

        expected_filename = "14-introduction.html"
        saved_path = pathlib.Path(self._tmpdir.name) / expected_filename
        self.assertTrue(saved_path.exists())
        # The cleaning step still runs: comments div must be gone.
        cleaned = saved_path.read_text(encoding="utf-8")
        self.assertNotIn('id="comments"', cleaned)
        self.assertIn("lesson content", cleaned)
        submit_conversion.assert_called_once_with(expected_filename)


if __name__ == "__main__":
    unittest.main()
