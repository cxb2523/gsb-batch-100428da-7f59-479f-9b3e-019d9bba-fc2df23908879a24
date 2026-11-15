"""Offline tests for get_url_list_from_site / set_url_list_to_file.

These tests never touch the real network: requests.get is replaced with a
fake object that serves a fixed HTML fragment. Run with:

    python -m unittest discover -s tests -v
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

# The source module is named "website-to-pdf.py", which is not a valid Python
# module identifier, so load it explicitly by path.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(REPO_ROOT, "website-to-pdf.py")

# The source module imports pdfkit and builds a configuration at import time.
# Link collection does not need wkhtmltopdf, so pdfkit is always stubbed here.
# This keeps the tests fully offline and independent of any system-level
# wkhtmltopdf installation.
pdfkit_stub = mock.MagicMock()
pdfkit_stub.configuration.return_value = mock.MagicMock()
sys.modules["pdfkit"] = pdfkit_stub

spec = importlib.util.spec_from_file_location("website_to_pdf", MODULE_PATH)
website_to_pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(website_to_pdf)

HOMEPAGE_URL = "https://example.com/site/index.html"

HTML_FRAGMENT = """
<html>
  <body>
    <!-- Absolute http/https links -->
    <a href="https://example.com/about/">About</a>
    <a href="http://other.example.com/page">Other site</a>

    <!-- Root-relative link (starts with /) -->
    <a href="/contact">Contact</a>

    <!-- Relative link containing ../ fallback -->
    <a href="../products/widget">Product</a>

    <!-- Plain relative link -->
    <a href="details.html">Details</a>

    <!-- Duplicates of links already listed above -->
    <a href="https://example.com/about/">About again</a>
    <a href="/contact">Contact again</a>

    <!-- Anchor without href -->
    <a>No href at all</a>
    <a href="">Empty href</a>
    <a href="#section">Fragment only</a>

    <!-- Non-http(s) links that must be skipped -->
    <a href="javascript:alert(1)">JavaScript</a>
    <a href="mailto:user@example.com">Mail</a>
    <a href="tel:+15551234567">Telephone</a>
  </body>
</html>
"""

EXPECTED_URLS = [
    "https://example.com/about/",
    "http://other.example.com/page",
    "https://example.com/contact",
    "https://example.com/products/widget",
    "https://example.com/site/details.html",
]


class FakeResponse:
    """Minimal stand-in for the response object returned by requests.get."""

    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise website_to_pdf.requests.exceptions.HTTPError(
                "HTTP status code: {}".format(self.status_code)
            )


class GetUrlListFromSiteTests(unittest.TestCase):
    def test_collects_resolves_deduplicates_and_filters_links(self):
        with mock.patch.object(
            website_to_pdf.requests,
            "get",
            return_value=FakeResponse(HTML_FRAGMENT),
        ) as mocked_get:
            result = website_to_pdf.get_url_list_from_site(HOMEPAGE_URL)

        self.assertEqual(result, EXPECTED_URLS)
        # No duplicates may survive into the PDF work list.
        self.assertEqual(len(result), len(set(result)))
        # Every collected link must be an absolute http/https address.
        for url in result:
            self.assertTrue(url.startswith("http://") or url.startswith("https://"))
        # The homepage itself is what gets requested.
        mocked_get.assert_called_once()
        self.assertEqual(mocked_get.call_args.args[0], HOMEPAGE_URL)

    def test_root_relative_link_on_slash_terminated_homepage(self):
        html = '<a href="/root/path">root</a>'
        with mock.patch.object(
            website_to_pdf.requests, "get", return_value=FakeResponse(html)
        ):
            result = website_to_pdf.get_url_list_from_site("https://example.com/")
        self.assertEqual(result, ["https://example.com/root/path"])

    def test_parent_fallback_is_resolved_against_homepage_path(self):
        html = '<a href="../../up/file.html">up</a>'
        with mock.patch.object(
            website_to_pdf.requests, "get", return_value=FakeResponse(html)
        ):
            result = website_to_pdf.get_url_list_from_site(
                "https://example.com/a/b/c/page.html"
            )
        self.assertEqual(result, ["https://example.com/a/up/file.html"])

    def test_homepage_connection_failure_returns_empty_list(self):
        with mock.patch.object(
            website_to_pdf.requests,
            "get",
            side_effect=website_to_pdf.requests.exceptions.ConnectionError(
                "boom"
            ),
        ):
            result = website_to_pdf.get_url_list_from_site(HOMEPAGE_URL)
        self.assertEqual(result, [])

    def test_homepage_http_error_returns_empty_list(self):
        with mock.patch.object(
            website_to_pdf.requests,
            "get",
            return_value=FakeResponse("not found", status_code=404),
        ):
            result = website_to_pdf.get_url_list_from_site(HOMEPAGE_URL)
        self.assertEqual(result, [])

    def test_results_can_be_written_to_url_file(self):
        with mock.patch.object(
            website_to_pdf.requests,
            "get",
            return_value=FakeResponse(HTML_FRAGMENT),
        ):
            urls = website_to_pdf.get_url_list_from_site(HOMEPAGE_URL)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "urls_from_site.txt")
            website_to_pdf.set_url_list_to_file(urls, output_file)
            with open(output_file, "r", encoding="UTF-8") as handle:
                written = [line.strip() for line in handle if line.strip()]

        self.assertEqual(written, EXPECTED_URLS)
        self.assertEqual(len(written), len(set(written)))


if __name__ == "__main__":
    unittest.main()
