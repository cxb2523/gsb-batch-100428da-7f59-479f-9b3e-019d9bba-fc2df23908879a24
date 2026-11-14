"""Tests for MagicGoogle.search_batch using locally built HTML pages.

No real Google request is made: every test overrides ``fetch_page`` with an
in-memory page map keyed by ``(query, start)``.
"""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from magic_google import (  # noqa: E402
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_NETWORK_ERROR,
    STATUS_PAGE_LIMIT,
    MagicGoogle,
)

try:
    from unittest import mock
except ImportError:  # pragma: no cover - python 2 compatibility
    import mock

try:
    from urllib.parse import parse_qs, urlparse
except ImportError:  # pragma: no cover - python 2 compatibility
    from urlparse import parse_qs, urlparse


EMPTY_PAGE = "<html><body><div id='search'></div></body></html>"


class FakeResponse:
    """Minimal ``requests.Response`` stand-in."""

    def __init__(self, content, status_code=200, headers=None):
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}


def build_result_page(urls, no_href_indexes=()):
    """Build a Google-like results page with one anchor per url.

    Args:
        urls (list[str]): result urls rendered as Google redirect links.
        no_href_indexes (iterable[int]): indexes rendered as anchors without href.
    """
    blocks = []
    for index, url in enumerate(urls):
        if index in no_href_indexes:
            anchor = '<a class="no-href"><h3>no href</h3></a>'
        else:
            anchor = (
                '<a href="/url?q={url}&amp;sa=U"><h3>title {index}</h3>'
                '<div class="url">https://example.com</div></a>'
            ).format(url=url, index=index)
        blocks.append('<div class="g"><div class="r">' + anchor + "</div></div>")
    return '<html><body><div id="search">' + "".join(blocks) + "</div></body></html>"


class FakeMagicGoogle(MagicGoogle):
    """MagicGoogle that serves pre-built pages instead of hitting the network."""

    def __init__(self, pages=None, errors=None, default_page=EMPTY_PAGE):
        super(FakeMagicGoogle, self).__init__(proxies=None)
        self.pages = pages or {}
        self.errors = errors or {}
        self.default_page = default_page
        self.requested = []

    def fetch_page(self, url, pause=2):
        query, start = self._route(url)
        self.requested.append((query, start))
        error = self.errors.get((query, start))
        if error is not None:
            raise error
        return self._serve(self.pages.get((query, start), self.default_page))

    @staticmethod
    def _serve(content):
        if isinstance(content, FakeResponse):
            return content
        return FakeResponse(content)

    def requested_starts(self, query):
        return [start for name, start in self.requested if name == query]

    @staticmethod
    def _route(url):
        parsed = urlparse(url)
        query = parse_qs(parsed.query).get("q", [""])[0]
        start = int(parse_qs(parsed.query).get("start", ["0"])[0])
        return query, start


def page(*urls, **kwargs):
    return build_result_page(list(urls), **kwargs)


class TestSearchBatchNormal(unittest.TestCase):
    """Normal pagination and untouched existing entry points."""

    def test_paginates_until_empty_page(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): page("https://example.com/1", "https://example.com/2"),
                ("python", 10): page("https://example.com/3", "https://example.com/4"),
                ("python", 20): page(),
            }
        )

        result = mg.search_batch(["python"], pause=0, num=2, step=10)

        self.assertTrue(result["finished"])
        entry = result["results"][0]
        self.assertEqual(entry["query"], "python")
        self.assertEqual(entry["status"], STATUS_COMPLETED)
        self.assertEqual(entry["pages"], 3)
        self.assertEqual(
            entry["urls"],
            [
                "https://example.com/1",
                "https://example.com/2",
                "https://example.com/3",
                "https://example.com/4",
            ],
        )
        self.assertIsNone(entry["next_start"])
        self.assertTrue(entry["finished"])
        self.assertEqual(mg.requested_starts("python"), [0, 10, 20])

    def test_default_step_matches_num(self):
        mg = FakeMagicGoogle(
            pages={("python", 0): page("https://example.com/1"), ("python", 5): page()}
        )

        entry = mg.search_batch(["python"], pause=0, num=5)["results"][0]

        self.assertEqual(entry["pages"], 2)
        self.assertEqual(mg.requested_starts("python"), [0, 5])

    def test_multiple_queries_are_collected_independently(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): page("https://example.com/py"),
                ("python", 10): page(),
                ("golang", 0): page("https://example.com/go"),
                ("golang", 10): page("https://example.com/go2"),
                ("golang", 20): page(),
            }
        )

        result = mg.search_batch(["python", "golang"], pause=0)

        entries = {entry["query"]: entry for entry in result["results"]}
        self.assertEqual(entries["python"]["pages"], 2)
        self.assertEqual(entries["python"]["urls"], ["https://example.com/py"])
        self.assertEqual(entries["golang"]["pages"], 3)
        self.assertEqual(
            entries["golang"]["urls"],
            ["https://example.com/go", "https://example.com/go2"],
        )
        self.assertTrue(result["finished"])

    def test_respects_max_pages(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): page("https://example.com/1"),
                ("python", 10): page("https://example.com/2"),
                ("python", 20): page("https://example.com/3"),
            }
        )

        entry = mg.search_batch(["python"], pause=0, max_pages=2)["results"][0]

        self.assertEqual(entry["status"], STATUS_PAGE_LIMIT)
        self.assertEqual(entry["pages"], 2)
        self.assertEqual(
            entry["urls"], ["https://example.com/1", "https://example.com/2"]
        )
        self.assertEqual(entry["next_start"], 20)
        self.assertFalse(entry["finished"])
        self.assertEqual(mg.requested_starts("python"), [0, 10])

    def test_skips_anchor_without_href(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): page(
                    "https://example.com/1",
                    "https://example.com/2",
                    no_href_indexes=(0,),
                )
            }
        )

        entry = mg.search_batch(["python"], pause=0, max_pages=1)["results"][0]

        self.assertEqual(entry["urls"], ["https://example.com/2"])

    def test_deduplicates_urls_across_pages(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): page("https://example.com/1", "https://example.com/2"),
                ("python", 10): page("https://example.com/2", "https://example.com/3"),
                ("python", 20): page(),
            }
        )

        entry = mg.search_batch(["python"], pause=0)["results"][0]

        self.assertEqual(
            entry["urls"],
            [
                "https://example.com/1",
                "https://example.com/2",
                "https://example.com/3",
            ],
        )

    def test_paging_urls_carry_start_once_and_skip_num_none(self):
        mg = MagicGoogle()

        first = mg._build_search_url("python")
        paged = mg._build_search_url("python", start=10)
        with_num = mg._build_search_url("python", num=10, start=10)

        self.assertNotIn("num=", first)
        self.assertNotIn("None", first)
        self.assertEqual(paged.count("start="), 1)
        self.assertNotIn("num=", paged)
        self.assertNotIn("None", paged)
        self.assertEqual(with_num.count("num="), 1)
        self.assertEqual(with_num.count("start="), 1)

    def test_existing_entry_points_keep_their_shape(self):
        html = page("https://example.com/1", "https://example.com/2")
        mg = FakeMagicGoogle(pages={("python", 0): html})

        self.assertEqual(mg.search_page("python", pause=0), html)
        self.assertEqual(
            list(mg.search_url("python", pause=0)),
            ["https://example.com/1", "https://example.com/2"],
        )
        results = list(mg.search("python", pause=0))
        self.assertEqual(
            [item["url"] for item in results],
            ["https://example.com/1", "https://example.com/2"],
        )
        self.assertEqual(
            sorted(results[0].keys()), ["text", "title", "url", "url_path"]
        )


class TestSearchBatchFailures(unittest.TestCase):
    """One query failing must not stop the others."""

    def test_verification_redirect_blocks_only_that_query(self):
        verification = FakeResponse(
            b"<html>moved</html>",
            status_code=302,
            headers={"Location": "https://ipv4.google.com/sorry/index?continue=x"},
        )
        mg = FakeMagicGoogle(
            pages={
                ("blocked", 0): verification,
                ("fine", 0): page("https://example.com/a"),
                ("fine", 10): page(),
            }
        )

        result = mg.search_batch(["blocked", "fine"], pause=0)
        entries = {entry["query"]: entry for entry in result["results"]}

        self.assertEqual(entries["blocked"]["status"], STATUS_BLOCKED)
        self.assertEqual(entries["blocked"]["pages"], 0)
        self.assertEqual(entries["blocked"]["urls"], [])
        self.assertIn("verification", entries["blocked"]["reason"])
        self.assertEqual(entries["fine"]["status"], STATUS_COMPLETED)
        self.assertEqual(entries["fine"]["urls"], ["https://example.com/a"])
        self.assertFalse(result["finished"])

    def test_verification_html_is_detected(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): (
                    '<html><body><form id="captcha">'
                    "<p>Our systems have detected unusual traffic from your "
                    "computer network.</p></form></body></html>"
                )
            }
        )

        entry = mg.search_batch(["python"], pause=0)["results"][0]

        self.assertEqual(entry["status"], STATUS_BLOCKED)
        self.assertEqual(entry["pages"], 0)
        self.assertEqual(entry["urls"], [])

    def test_result_page_mentioning_captcha_is_not_blocked(self):
        snippet_page = (
            '<html><body><div class="g"><div class="r">'
            '<a href="/url?q=https://example.com/captcha&amp;sa=U">'
            "<h3>how to solve a g-recaptcha</h3>"
            '<div class="url">https://example.com</div></a></div></div>'
            "</div></body></html>"
        )
        mg = FakeMagicGoogle(
            pages={("captcha", 0): snippet_page, ("captcha", 10): page()}
        )

        entry = mg.search_batch(["captcha"], pause=0)["results"][0]

        self.assertEqual(entry["status"], STATUS_COMPLETED)
        self.assertEqual(entry["pages"], 2)
        self.assertEqual(entry["urls"], ["https://example.com/captcha"])

    def test_http_200_moved_body_to_sorry_page_is_blocked(self):
        # This is the body Google returns in the README example: HTTP 200 with a
        # link to the verification page and no search results.
        moved_body = (
            "<HTML><HEAD><TITLE>302 Moved</TITLE></HEAD><BODY><H1>302 Moved</H1>"
            'The document has moved <A HREF="https://ipv4.google.com/sorry/index'
            '?continue=https://www.google.me/s">here</A>.</BODY></HTML>'
        )
        mg = FakeMagicGoogle(pages={("python", 0): moved_body})

        entry = mg.search_batch(["python"], pause=0)["results"][0]

        self.assertEqual(entry["status"], STATUS_BLOCKED)
        self.assertEqual(entry["pages"], 0)
        self.assertEqual(entry["urls"], [])

    def test_http_403_is_blocked(self):
        mg = FakeMagicGoogle(
            pages={("python", 0): FakeResponse(b"nope", status_code=403)}
        )

        self.assertEqual(
            mg.search_batch(["python"], pause=0)["results"][0]["status"],
            STATUS_BLOCKED,
        )

    def test_network_error_ends_only_that_query(self):
        mg = FakeMagicGoogle(
            pages={
                ("fine", 0): page("https://example.com/b"),
                ("fine", 10): page(),
            },
            errors={("boom", 0): ConnectionError("connection reset by peer")},
        )

        result = mg.search_batch(["boom", "fine"], pause=0)
        entries = {entry["query"]: entry for entry in result["results"]}

        self.assertEqual(entries["boom"]["status"], STATUS_NETWORK_ERROR)
        self.assertEqual(entries["boom"]["pages"], 0)
        self.assertIn("ConnectionError", entries["boom"]["reason"])
        self.assertEqual(entries["boom"]["next_start"], 0)
        self.assertEqual(entries["fine"]["status"], STATUS_COMPLETED)
        self.assertEqual(entries["fine"]["urls"], ["https://example.com/b"])

    def test_network_error_keeps_pages_collected_so_far(self):
        mg = FakeMagicGoogle(
            pages={("python", 0): page("https://example.com/1")},
            errors={("python", 10): ConnectionError("timed out")},
        )

        entry = mg.search_batch(["python"], pause=0)["results"][0]

        self.assertEqual(entry["status"], STATUS_NETWORK_ERROR)
        self.assertEqual(entry["pages"], 1)
        self.assertEqual(entry["urls"], ["https://example.com/1"])
        self.assertEqual(entry["next_start"], 10)

    def test_empty_first_page_completes_with_zero_urls(self):
        mg = FakeMagicGoogle(pages={("python", 0): page()})

        entry = mg.search_batch(["python"], pause=0)["results"][0]

        self.assertEqual(entry["status"], STATUS_COMPLETED)
        self.assertEqual(entry["pages"], 1)
        self.assertEqual(entry["urls"], [])


class TestSearchBatchResume(unittest.TestCase):
    """Resuming must continue after the last completed page."""

    def test_resume_continues_and_skips_finished_queries(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): page("https://example.com/1"),
                ("python", 10): page("https://example.com/2"),
                ("python", 20): page(),
                ("golang", 0): page("https://example.com/go"),
                ("golang", 10): page(),
            }
        )

        first = mg.search_batch(["python"], pause=0, max_pages=1)
        self.assertEqual(mg.requested_starts("python"), [0])
        self.assertEqual(first["results"][0]["status"], STATUS_PAGE_LIMIT)

        mg.requested = []
        second = mg.search_batch(["python", "golang"], pause=0, previous=first)

        # "python" resumes at start=10; "golang" is new and starts at 0.
        self.assertEqual(mg.requested, [("python", 10), ("python", 20), ("golang", 0), ("golang", 10)])
        entries = {entry["query"]: entry for entry in second["results"]}
        self.assertEqual(entries["python"]["status"], STATUS_COMPLETED)
        self.assertEqual(entries["python"]["pages"], 3)
        self.assertEqual(
            entries["python"]["urls"],
            ["https://example.com/1", "https://example.com/2"],
        )
        self.assertEqual(entries["golang"]["status"], STATUS_COMPLETED)

        # A third run has nothing left to request for completed queries.
        mg.requested = []
        third = mg.search_batch(["python"], pause=0, previous=second)
        self.assertEqual(mg.requested, [])
        self.assertEqual(third["results"][0]["status"], STATUS_COMPLETED)
        self.assertEqual(
            third["results"][0]["urls"],
            ["https://example.com/1", "https://example.com/2"],
        )

    def test_resume_retries_the_page_that_failed(self):
        hook = {"fail": True}

        class FlakyMagicGoogle(FakeMagicGoogle):
            def fetch_page(self, url, pause=2):
                query, start = self._route(url)
                self.requested.append((query, start))
                if start == 10 and hook["fail"]:
                    raise ConnectionError("temporary failure")
                return self._serve(self.pages.get((query, start), self.default_page))

        mg = FlakyMagicGoogle(
            pages={
                ("python", 0): page("https://example.com/1"),
                ("python", 10): page("https://example.com/2"),
                ("python", 20): page(),
            }
        )

        first = mg.search_batch(["python"], pause=0)
        self.assertEqual(first["results"][0]["status"], STATUS_NETWORK_ERROR)
        self.assertEqual(first["results"][0]["next_start"], 10)

        hook["fail"] = False
        mg.requested = []
        second = mg.search_batch(["python"], pause=0, previous=first)

        self.assertEqual(mg.requested_starts("python"), [10, 20])
        self.assertEqual(second["results"][0]["status"], STATUS_COMPLETED)
        self.assertEqual(
            second["results"][0]["urls"],
            ["https://example.com/1", "https://example.com/2"],
        )

    def test_resume_accepts_the_results_list(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): page("https://example.com/1"),
                ("python", 10): page(),
            }
        )

        first = mg.search_batch(["python"], pause=0, max_pages=1)
        mg.requested = []
        second = mg.search_batch(["python"], pause=0, previous=first["results"])

        self.assertEqual(mg.requested_starts("python"), [10])
        self.assertEqual(second["results"][0]["status"], STATUS_COMPLETED)

    def test_resume_gets_a_fresh_page_budget(self):
        mg = FakeMagicGoogle(
            pages={
                ("python", 0): page("https://example.com/1"),
                ("python", 10): page("https://example.com/2"),
                ("python", 20): page("https://example.com/3"),
                ("python", 30): page(),
            }
        )

        first = mg.search_batch(["python"], pause=0, max_pages=1)
        second = mg.search_batch(["python"], pause=0, max_pages=2, previous=first)

        self.assertEqual(second["results"][0]["status"], STATUS_PAGE_LIMIT)
        # ``pages`` accumulates across runs while only two new pages were fetched.
        self.assertEqual(second["results"][0]["pages"], 3)
        self.assertEqual(mg.requested_starts("python"), [0, 10, 20])

        third = mg.search_batch(["python"], pause=0, max_pages=1, previous=second)
        self.assertEqual(third["results"][0]["status"], STATUS_COMPLETED)
        self.assertEqual(third["results"][0]["pages"], 4)
        self.assertEqual(
            third["results"][0]["urls"],
            [
                "https://example.com/1",
                "https://example.com/2",
                "https://example.com/3",
            ],
        )


class TestSearchBatchOverHttpLayer(unittest.TestCase):
    """Exercise the real ``fetch_page``/url building with mocked HTTP calls."""

    def setUp(self):
        self.mg = MagicGoogle(proxies=None)

    def test_requests_are_sequential_pages_of_each_query(self):
        pages = {
            0: page("https://example.com/1"),
            10: page("https://example.com/2"),
            20: page(),
        }
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            start = int(url.split("start=", 1)[1].split("&", 1)[0]) if "start=" in url else 0
            return FakeResponse(pages[start])

        with mock.patch("magic_google.search.requests.get", side_effect=fake_get):
            result = self.mg.search_batch(["python", "golang"], pause=0)

        # Two keywords, three pages each; urls carry the expected offsets.
        self.assertEqual(len(calls), 6)
        self.assertEqual(
            [call.split("start=")[1].split("&")[0] for call in calls if "start=" in call],
            ["10", "20", "10", "20"],
        )
        self.assertTrue(all("q=python" in call or "q=golang" in call for call in calls))
        self.assertTrue(result["finished"])
        self.assertEqual(
            [entry["urls"] for entry in result["results"]],
            [["https://example.com/1", "https://example.com/2"]] * 2,
        )

    def test_verification_redirect_is_reported_as_blocked(self):
        import requests

        response = FakeResponse(
            b"<html>moved</html>",
            status_code=302,
            headers={"Location": "https://www.google.com/sorry/index?continue=x"},
        )

        with mock.patch("magic_google.search.requests.get", return_value=response):
            result = self.mg.search_batch(["python"], pause=0)

        entry = result["results"][0]
        self.assertEqual(entry["status"], STATUS_BLOCKED)
        self.assertIn("verification", entry["reason"])

    def test_exception_from_requests_is_reported(self):
        import requests

        with mock.patch(
            "magic_google.search.requests.get",
            side_effect=requests.exceptions.ConnectionError("proxy refused"),
        ):
            result = self.mg.search_batch(["python"], pause=0)

        entry = result["results"][0]
        self.assertEqual(entry["status"], STATUS_NETWORK_ERROR)
        self.assertIn("ConnectionError", entry["reason"])


class TestSearchBatchValidation(unittest.TestCase):
    """Invalid arguments fail fast instead of silently mis-paginating."""

    def setUp(self):
        self.mg = FakeMagicGoogle()

    def test_rejects_empty_queries(self):
        with self.assertRaises(ValueError):
            self.mg.search_batch([], pause=0)

    def test_rejects_non_string_query(self):
        with self.assertRaises(ValueError):
            self.mg.search_batch(["python", None], pause=0)

    def test_rejects_bad_limits(self):
        with self.assertRaises(ValueError):
            self.mg.search_batch(["python"], pause=0, max_pages=0)
        with self.assertRaises(ValueError):
            self.mg.search_batch(["python"], pause=0, step=0)
        with self.assertRaises(ValueError):
            self.mg.search_batch(["python"], pause=0, start=-1)

    def test_rejects_bad_previous(self):
        with self.assertRaises(ValueError):
            self.mg.search_batch(["python"], pause=0, previous={"oops": 1})
        with self.assertRaises(ValueError):
            self.mg.search_batch(["python"], pause=0, previous=[{"nope": 1}])

    def test_duplicate_queries_are_collapsed(self):
        mg = FakeMagicGoogle(
            pages={("python", 0): page("https://example.com/1"), ("python", 10): page()}
        )

        result = mg.search_batch(["python", "python"], pause=0)

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(mg.requested_starts("python"), [0, 10])


if __name__ == "__main__":
    unittest.main()
