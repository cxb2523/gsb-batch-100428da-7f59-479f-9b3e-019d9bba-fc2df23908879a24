"""Network-free tests for paginated ``Pinterest.search``.

A fake session/response pair stands in for ``requests`` so no real network
traffic is produced. Each fake resource response also carries a pagination
cursor so the tests can exercise Pinterest's bookmark-based paging.
"""
from urllib.parse import urlparse, parse_qs, unquote

import pytest

from pinscrape.v2 import Pinterest


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeSession:
    """Records every GET and serves scripted responses for resource calls."""

    def __init__(self, resource_responses):
        # resource_responses: list consumed in order for BaseSearchResource calls.
        self.resource_responses = list(resource_responses)
        self.calls = []
        self.warmup_calls = []

    def get(self, url, headers=None, proxies=None, timeout=None, **kwargs):
        recorded = {"url": url, "headers": headers, "proxies": proxies, "timeout": timeout}
        if "/resource/BaseSearchResource/get/" in url:
            self.calls.append(recorded)
            if not self.resource_responses:
                raise AssertionError("search requested more pages than were scripted")
            return self.resource_responses.pop(0)
        # Anything else (e.g. the warm-up request) just succeeds.
        self.warmup_calls.append(recorded)
        return FakeResponse(status_code=200, payload={})


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _pin(orig_url=None, include_orig=True):
    images = {}
    if include_orig:
        images["orig"] = {"url": orig_url or "https://i.pinimg.com/originals/x/y/z/abc.jpg"}
    return {"images": images}


def search_payload(urls=None, bookmark=None, results=None):
    if results is None:
        results = [{"images": {"orig": {"url": u}}} for u in (urls or [])]
    return {
        "resource_response": {
            "data": {
                "results": results,
                "bookmark": bookmark,
            }
        },
        "client_context": {},
    }


def make_pinterest(session):
    p = Pinterest()
    p.session = session
    return p


def request_bookmark(url):
    """Extract the bookmarks cursor encoded in a search request URL (or None)."""
    data_encoded = parse_qs(urlparse(url).query)["data"][0]
    data = unquote(data_encoded)
    import json
    options = json.loads(data)["options"]
    bookmarks = options.get("bookmarks")
    if not bookmarks:
        return None
    return bookmarks[0]


# ---------------------------------------------------------------------------
# Multi-page happy path
# ---------------------------------------------------------------------------

def test_multi_page_pagination_follows_cursors_and_dedupes():
    page1 = search_payload(
        urls=[
            "https://i.pinimg.com/originals/a/1.jpg",
            "https://i.pinimg.com/originals/a/2.jpg",
        ],
        bookmark="cursor-page-2",
    )
    page2 = search_payload(
        urls=[
            "https://i.pinimg.com/originals/b/3.jpg",
            "https://i.pinimg.com/originals/a/1.jpg",  # duplicate of a page-1 URL
        ],
        bookmark="cursor-page-3",
    )
    page3 = search_payload(
        urls=["https://i.pinimg.com/originals/c/4.jpg"],
        bookmark="-end-",  # Pinterest's terminal marker: paging must stop
    )
    session = FakeSession([
        FakeResponse(200, page1),
        FakeResponse(200, page2),
        FakeResponse(200, page3),
    ])
    p = make_pinterest(session)

    urls = p.search("loki", max_pages=5)

    # Three resource requests: the "-end-" cursor stops paging before max_pages.
    assert len(session.calls) == 3
    assert request_bookmark(session.calls[0]["url"]) is None
    assert request_bookmark(session.calls[1]["url"]) == "cursor-page-2"
    assert request_bookmark(session.calls[2]["url"]) == "cursor-page-3"

    # First-appearance ordering, cross-page duplicate removed.
    assert urls == [
        "https://i.pinimg.com/originals/a/1.jpg",
        "https://i.pinimg.com/originals/a/2.jpg",
        "https://i.pinimg.com/originals/b/3.jpg",
        "https://i.pinimg.com/originals/c/4.jpg",
    ]

    outcome = p.last_search
    assert outcome["found_image"] is True
    assert outcome["pages_requested"] == 3
    assert [r["page"] for r in outcome["page_results"]] == [1, 2, 3]
    assert all(r["outcome"] == Pinterest.PAGE_SUCCESS for r in outcome["page_results"])
    # images_count counts images on successful pages before cross-page dedupe.
    assert outcome["images_count"] == 2 + 2 + 1
    assert [r["images"] for r in outcome["page_results"]] == [2, 2, 1]


def test_max_pages_caps_the_number_of_requests():
    pages = [
        FakeResponse(200, search_payload(urls=[f"https://i.pinimg.com/originals/{n}.jpg"],
                                         bookmark=f"cur-{n}"))
        for n in range(5)
    ]
    session = FakeSession(pages)
    p = make_pinterest(session)

    urls = p.search("thor", max_pages=2)

    assert len(session.calls) == 2
    assert len(urls) == 2
    assert p.last_search["pages_requested"] == 2
    assert request_bookmark(session.calls[1]["url"]) == "cur-0"


def test_default_search_requests_only_one_page():
    session = FakeSession([
        FakeResponse(200, search_payload(
            urls=["https://i.pinimg.com/originals/one.jpg"], bookmark="next-cursor"
        )),
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/two.jpg"])),
    ])
    p = make_pinterest(session)

    urls = p.search("odin")  # no max_pages -> historical single-page behaviour

    assert len(session.calls) == 1
    assert urls == ["https://i.pinimg.com/originals/one.jpg"]
    assert p.last_search["pages_requested"] == 1
    assert p.last_search["page_results"][0]["outcome"] == Pinterest.PAGE_SUCCESS


def test_stops_when_no_cursor_is_returned():
    session = FakeSession([
        FakeResponse(200, search_payload(
            urls=["https://i.pinimg.com/originals/only.jpg"], bookmark=None
        )),
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/never.jpg"])),
    ])
    p = make_pinterest(session)

    urls = p.search("heimdall", max_pages=5)

    assert len(session.calls) == 1
    assert urls == ["https://i.pinimg.com/originals/only.jpg"]


def test_empty_string_bookmark_is_treated_as_no_cursor():
    session = FakeSession([
        FakeResponse(200, search_payload(
            urls=["https://i.pinimg.com/originals/only.jpg"], bookmark=""
        )),
    ])
    p = make_pinterest(session)

    p.search("heimdall", max_pages=5)

    assert len(session.calls) == 1


# ---------------------------------------------------------------------------
# Per-page failure handling
# ---------------------------------------------------------------------------

def test_http_error_on_middle_page_does_not_stop_the_run():
    good1 = FakeResponse(200, search_payload(
        urls=["https://i.pinimg.com/originals/g1.jpg"], bookmark="to-bad-page"
    ))
    # The failure body still carries a usable cursor, so the run continues to
    # page 3 even though the page itself is unusable.
    bad_with_cursor = FakeResponse(503, search_payload(
        urls=[], bookmark="to-page-3"
    ))
    good2 = FakeResponse(200, search_payload(
        urls=["https://i.pinimg.com/originals/g2.jpg"], bookmark="-end-"
    ))
    session = FakeSession([good1, bad_with_cursor, good2])
    p = make_pinterest(session)

    urls = p.search("hulk", max_pages=3)

    assert len(session.calls) == 3
    assert urls == [
        "https://i.pinimg.com/originals/g1.jpg",
        "https://i.pinimg.com/originals/g2.jpg",
    ]
    results = p.last_search["page_results"]
    assert [r["outcome"] for r in results] == [
        Pinterest.PAGE_SUCCESS,
        Pinterest.PAGE_HTTP_ERROR,
        Pinterest.PAGE_SUCCESS,
    ]
    assert results[1]["page"] == 2 and results[1]["images"] == 0
    assert p.last_search["found_image"] is True
    # The failed page contributes nothing to the successful-page image total.
    assert p.last_search["images_count"] == 2


def test_http_error_without_cursor_ends_paging_gracefully():
    session = FakeSession([
        FakeResponse(200, search_payload(
            urls=["https://i.pinimg.com/originals/g1.jpg"], bookmark="to-bad"
        )),
        FakeResponse(429, {"oops": "no bookmark here"}),
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/never.jpg"])),
    ])
    p = make_pinterest(session)

    urls = p.search("vision", max_pages=5)

    assert len(session.calls) == 2
    assert urls == ["https://i.pinimg.com/originals/g1.jpg"]
    assert [r["outcome"] for r in p.last_search["page_results"]] == [
        Pinterest.PAGE_SUCCESS,
        Pinterest.PAGE_HTTP_ERROR,
    ]


def test_network_exception_is_recorded_as_http_error():
    class RaisingSession(FakeSession):
        def get(self, url, headers=None, proxies=None, timeout=None, **kwargs):
            if "/resource/BaseSearchResource/get/" in url:
                self.calls.append({"url": url})
                raise ConnectionError("connection reset")
            return FakeResponse(200, {})

    p = make_pinterest(RaisingSession([]))

    urls = p.search("wanda", max_pages=3)

    assert urls == []
    result = p.last_search["page_results"][0]
    assert result["outcome"] == Pinterest.PAGE_HTTP_ERROR
    assert p.last_search["found_image"] is False
    assert p.last_search["images_count"] == 0


def test_unparseable_json_is_parse_error_and_run_continues():
    good = FakeResponse(200, search_payload(
        urls=["https://i.pinimg.com/originals/g1.jpg"], bookmark="to-bad-json"
    ))
    # pydantic rejects this (client_context missing) -> parse_error, while the
    # safe extractor still finds the cursor so the run can recover on page 3.
    schema_bad = FakeResponse(200, {
        "resource_response": {"data": {"bookmark": "to-recovery"}},
    })
    recover = FakeResponse(200, search_payload(
        urls=["https://i.pinimg.com/originals/g2.jpg"], bookmark="-end-"
    ))
    session = FakeSession([good, schema_bad, recover])
    p = make_pinterest(session)

    urls = p.search("hawkeye", max_pages=3)

    assert len(session.calls) == 3
    assert urls == [
        "https://i.pinimg.com/originals/g1.jpg",
        "https://i.pinimg.com/originals/g2.jpg",
    ]
    assert [r["outcome"] for r in p.last_search["page_results"]] == [
        Pinterest.PAGE_SUCCESS,
        Pinterest.PAGE_PARSE_ERROR,
        Pinterest.PAGE_SUCCESS,
    ]


def test_parse_error_without_cursor_keeps_earlier_images():
    session = FakeSession([
        FakeResponse(200, search_payload(
            urls=["https://i.pinimg.com/originals/keep.jpg"], bookmark="to-bad"
        )),
        FakeResponse(200, json_error=ValueError("not json")),
    ])
    p = make_pinterest(session)

    urls = p.search("natasha", max_pages=5)

    assert urls == ["https://i.pinimg.com/originals/keep.jpg"]
    assert [r["outcome"] for r in p.last_search["page_results"]] == [
        Pinterest.PAGE_SUCCESS,
        Pinterest.PAGE_PARSE_ERROR,
    ]
    # Failed page must not wipe images collected from the successful page.
    assert p.last_search["found_image"] is True
    assert p.last_search["images_count"] == 1


def test_empty_data_is_recorded_and_keeps_earlier_images():
    session = FakeSession([
        FakeResponse(200, search_payload(
            urls=["https://i.pinimg.com/originals/keep.jpg"], bookmark="to-empty"
        )),
        FakeResponse(200, {"resource_response": {"data": None}, "client_context": {}}),
    ])
    p = make_pinterest(session)

    urls = p.search("fury", max_pages=5)

    assert urls == ["https://i.pinimg.com/originals/keep.jpg"]
    assert [r["outcome"] for r in p.last_search["page_results"]] == [
        Pinterest.PAGE_SUCCESS,
        Pinterest.PAGE_EMPTY_DATA,
    ]
    assert p.last_search["images_count"] == 1


def test_empty_results_list_is_also_empty_data():
    session = FakeSession([
        FakeResponse(200, search_payload(results=[], bookmark=None)),
    ])
    p = make_pinterest(session)

    urls = p.search("empty", max_pages=3)

    assert urls == []
    assert len(session.calls) == 1
    assert p.last_search["page_results"][0]["outcome"] == Pinterest.PAGE_EMPTY_DATA


def test_page_without_orig_images_is_recorded_and_run_continues():
    good = FakeResponse(200, search_payload(
        urls=["https://i.pinimg.com/originals/g1.jpg"], bookmark="to-no-orig"
    ))
    no_orig = FakeResponse(200, search_payload(
        bookmark="to-final",
        results=[
            {"images": {}},  # no orig key
            {"images": None},  # no images at all
            _pin(include_orig=False),  # images dict without orig
        ],
    ))
    final = FakeResponse(200, search_payload(
        urls=["https://i.pinimg.com/originals/g2.jpg"], bookmark=None
    ))
    session = FakeSession([good, no_orig, final])
    p = make_pinterest(session)

    urls = p.search("rocket", max_pages=3)

    assert urls == [
        "https://i.pinimg.com/originals/g1.jpg",
        "https://i.pinimg.com/originals/g2.jpg",
    ]
    assert [r["outcome"] for r in p.last_search["page_results"]] == [
        Pinterest.PAGE_SUCCESS,
        Pinterest.PAGE_NO_ORIG_IMAGE,
        Pinterest.PAGE_SUCCESS,
    ]


def test_all_pages_failing_returns_empty_with_full_report():
    # Each failure body still exposes a cursor, so both requested pages are tried.
    session = FakeSession([
        FakeResponse(500, search_payload(urls=[], bookmark="cur-2")),
        FakeResponse(500, search_payload(urls=[], bookmark="-end-")),
    ])
    p = make_pinterest(session)

    urls = p.search("thanos", max_pages=2)

    assert urls == []
    outcome = p.last_search
    assert outcome["found_image"] is False
    assert outcome["pages_requested"] == 2
    assert outcome["images_count"] == 0
    assert [r["outcome"] for r in outcome["page_results"]] == [
        Pinterest.PAGE_HTTP_ERROR,
        Pinterest.PAGE_HTTP_ERROR,
    ]


# ---------------------------------------------------------------------------
# Request construction / behaviour preservation
# ---------------------------------------------------------------------------

def test_warmup_runs_once_with_base_headers():
    session = FakeSession([
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/x.jpg"])),
    ])
    p = make_pinterest(session)

    p.search("loki", max_pages=3)

    assert len(session.warmup_calls) == 1
    warmup = session.warmup_calls[0]
    assert warmup["url"] == f"{Pinterest.BASE_URL}/search/pins/?q=loki&rs=typed"
    assert warmup["headers"] is p.BASE_HEADERS


def test_search_request_uses_source_url_header_and_bookmark_payload():
    session = FakeSession([
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/x.jpg"],
                                         bookmark="cur-1")),
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/y.jpg"])),
    ])
    p = make_pinterest(session)

    p.search("spider man", max_pages=2)

    for call in session.calls:
        assert call["proxies"] == p.proxies
        assert call["timeout"] is not None
        assert call["headers"]["X-Pinterest-Source-Url"] == "/search/pins/?q=spider%20man&rs=typed"
        # Per-request headers must be derived from BASE_HEADERS (with the
        # source-url header overridden, exactly as the original implementation).
        for key, value in p.BASE_HEADERS.items():
            if key == "X-Pinterest-Source-Url":
                continue
            assert call["headers"][key] == value
    # The BASE_HEADERS template itself must not be mutated.
    assert p.BASE_HEADERS["X-Pinterest-Source-Url"] == ""


def test_first_page_payload_has_no_bookmarks_key():
    session = FakeSession([
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/x.jpg"])),
    ])
    p = make_pinterest(session)

    p.search("loki")

    import json
    data = json.loads(unquote(parse_qs(urlparse(session.calls[0]["url"]).query)["data"][0]))
    assert "bookmarks" not in data["options"]
    assert data["options"]["query"] == "loki"
    assert data["options"]["page_size"] == "26"
    assert data["options"]["scope"] == "pins"


def test_last_search_resets_on_each_call():
    session1 = FakeSession([
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/a.jpg"])),
    ])
    p = make_pinterest(session1)
    p.search("first")
    assert p.last_search["pages_requested"] == 1

    session2 = FakeSession([
        FakeResponse(500, search_payload(urls=[], bookmark="cur-x")),
        FakeResponse(200, search_payload(urls=["https://i.pinimg.com/originals/b.jpg"])),
    ])
    p.session = session2
    p.search("second", max_pages=2)

    assert p.last_search["pages_requested"] == 2
    assert [r["page"] for r in p.last_search["page_results"]] == [1, 2]
    assert p.last_search["found_image"] is True


def test_invalid_max_pages_rejected():
    p = make_pinterest(FakeSession([]))
    for bad in (0, -1, 1.5, "2", True):
        with pytest.raises(ValueError):
            p.search("loki", max_pages=bad)
