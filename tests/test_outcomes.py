"""Offline tests for per-page crawl outcomes.

Every response is built from a local HTML fragment, so no network,
Playwright browser, or Facebook session is needed:

    python -I -m pytest tests/ -v
"""

import json
import os

import pytest
from scrapy import Request
from scrapy.http import TextResponse

from facebook.outcomes import (
    NO_POST_ID,
    PARSE_ERROR,
    SESSION_EXPIRED,
    SUCCESS,
)
from facebook.spiders.fb_page import FbPageSpider

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
BASE_URL = "https://www.facebook.com/"


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


def _make_response(url: str, html: str, meta=None) -> TextResponse:
    """Build a Scrapy response from an in-memory HTML fragment."""
    request = Request(url=url, meta=meta or {})
    return TextResponse(
        url=url,
        encoding="utf-8",
        body=html.encode("utf-8"),
        request=request,
    )


def _make_spider(page_ids, tmp_path, output_name="outcomes.json") -> FbPageSpider:
    return FbPageSpider(
        page_id=",".join(page_ids),
        outcome_output=str(tmp_path / output_name),
    )


def _drain(generator):
    """Collect every object a parse callback yields."""
    return list(generator)


def _story_response(story_request: Request) -> TextResponse:
    """Turn the story request produced by parse_page into a post response."""
    return _make_response(
        story_request.url,
        _fixture("post_ok.html"),
        meta=dict(story_request.meta),
    )


def _load_report(spider: FbPageSpider) -> dict:
    with open(spider.outcome_recorder.output_path, encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


def test_success_outcome_references_produced_post(tmp_path):
    spider = _make_spider(["ejeab"], tmp_path)
    timeline = _make_response(
        BASE_URL + "ejeab",
        _fixture("timeline_ok.html"),
        meta={"page_id": "ejeab", "page_index": 0},
    )

    page_outputs = _drain(spider.parse_page(timeline))
    assert len(page_outputs) == 1
    story_request = page_outputs[0]
    assert isinstance(story_request, Request)
    assert story_request.callback == spider.parse_post

    post_outputs = _drain(spider.parse_post(_story_response(story_request)))
    items = [obj for obj in post_outputs if not isinstance(obj, Request)]
    assert len(items) == 1
    post_item = items[0]

    report = _load_report(spider)
    assert report["stop_reason"] is None
    assert len(report["outcomes"]) == 1
    outcome = report["outcomes"][0]
    assert outcome["page_id"] == "ejeab"
    assert outcome["page_url"] == BASE_URL + "ejeab"
    assert outcome["status"] == SUCCESS
    # The success outcome must point at the produced post entry.
    assert outcome["post_ids"] == ["999888"]
    assert outcome["post_urls"] == [story_request.url]
    assert post_item["post_url"] == story_request.url
    # Existing post field semantics are unchanged.
    assert post_item["page_id"] == "ejeab"
    assert post_item["reaction_count"] == 12
    assert post_item["comment_count"] == 1
    assert post_item["share_count"] == 3
    assert post_item["comments"][0]["comment_id"] == "700011"


# --------------------------------------------------------------------------
# Timeline without a story id
# --------------------------------------------------------------------------


def test_no_post_id_outcome_and_chain_continues(tmp_path):
    spider = _make_spider(["empty", "ejeab"], tmp_path)
    timeline = _make_response(
        BASE_URL + "empty",
        _fixture("timeline_no_id.html"),
        meta={"page_id": "empty", "page_index": 0},
    )

    outputs = _drain(spider.parse_page(timeline))
    # No story request is yielded for this page, but the next page is
    # still scheduled.
    assert len(outputs) == 1
    next_request = outputs[0]
    assert isinstance(next_request, Request)
    assert next_request.url == BASE_URL + "ejeab"
    assert next_request.callback == spider.parse_page

    report = _load_report(spider)
    assert report["stop_reason"] is None
    assert len(report["outcomes"]) == 1
    outcome = report["outcomes"][0]
    assert outcome["page_id"] == "empty"
    assert outcome["page_url"] == BASE_URL + "empty"
    assert outcome["status"] == NO_POST_ID
    assert outcome["post_ids"] == []
    assert outcome["post_urls"] == []


def test_no_post_id_on_last_page_yields_nothing(tmp_path):
    spider = _make_spider(["empty"], tmp_path)
    timeline = _make_response(
        BASE_URL + "empty",
        _fixture("timeline_no_id.html"),
        meta={"page_id": "empty", "page_index": 0},
    )

    assert _drain(spider.parse_page(timeline)) == []

    report = _load_report(spider)
    assert len(report["outcomes"]) == 1
    assert report["outcomes"][0]["status"] == NO_POST_ID


# --------------------------------------------------------------------------
# Session expired
# --------------------------------------------------------------------------


def test_session_expired_on_landing_records_every_page(tmp_path):
    spider = _make_spider(["ejeab", "empty"], tmp_path)
    landing = _make_response(BASE_URL, _fixture("login_form.html"))

    # No timeline request must be produced.
    assert _drain(spider.parse(landing)) == []
    assert spider.session_expired is True

    report = _load_report(spider)
    assert report["stop_reason"] is not None
    assert "session expired" in report["stop_reason"]
    assert [o["page_id"] for o in report["outcomes"]] == ["ejeab", "empty"]
    assert all(o["status"] == SESSION_EXPIRED for o in report["outcomes"])
    assert all(o["page_url"].startswith(BASE_URL) for o in report["outcomes"])


def test_session_expired_mid_run_stops_later_requests(tmp_path):
    # Three pages: the first succeeds, the second returns the login form.
    spider = _make_spider(["ejeab", "dead", "never"], tmp_path)

    # Page 1: success.
    timeline_1 = _make_response(
        BASE_URL + "ejeab",
        _fixture("timeline_ok.html"),
        meta={"page_id": "ejeab", "page_index": 0},
    )
    story_request_1 = _drain(spider.parse_page(timeline_1))[0]
    _drain(spider.parse_post(_story_response(story_request_1)))
    # The success page chains to page 2.
    assert spider.session_expired is False

    # Page 2: session expired on the timeline response.
    timeline_2 = _make_response(
        BASE_URL + "dead",
        _fixture("login_form.html"),
        meta={"page_id": "dead", "page_index": 1},
    )
    outputs = _drain(spider.parse_page(timeline_2))
    # No further request is yielded: page 3 is never requested.
    assert outputs == []
    assert spider.session_expired is True

    # Even if the engine were to ask, no later request is built.
    assert spider.next_page_request(1) is None

    report = _load_report(spider)
    assert report["stop_reason"] is not None
    statuses = [(o["page_id"], o["status"]) for o in report["outcomes"]]
    assert statuses == [
        ("ejeab", SUCCESS),
        ("dead", SESSION_EXPIRED),
        ("never", SESSION_EXPIRED),
    ]
    # The already-gathered success keeps its post reference.
    assert report["outcomes"][0]["post_ids"] == ["999888"]
    # Expiry outcomes never claim posts.
    for outcome in report["outcomes"][1:]:
        assert outcome["post_ids"] == []
        assert outcome["post_urls"] == []
        assert "login form" in outcome["detail"]


def test_session_expired_on_story_response(tmp_path):
    # Timeline of page 1 renders fine, but its story response is the
    # login form; the later page must not be requested.
    spider = _make_spider(["ejeab", "never"], tmp_path)
    timeline = _make_response(
        BASE_URL + "ejeab",
        _fixture("timeline_ok.html"),
        meta={"page_id": "ejeab", "page_index": 0},
    )
    story_request = _drain(spider.parse_page(timeline))[0]
    story_login = _make_response(
        story_request.url,
        _fixture("login_form.html"),
        meta=dict(story_request.meta),
    )

    assert _drain(spider.parse_post(story_login)) == []

    report = _load_report(spider)
    assert report["stop_reason"] is not None
    assert [(o["page_id"], o["status"]) for o in report["outcomes"]] == [
        ("ejeab", SESSION_EXPIRED),
        ("never", SESSION_EXPIRED),
    ]


# --------------------------------------------------------------------------
# Other parse anomaly
# --------------------------------------------------------------------------


def test_parse_error_outcome_when_timeline_blows_up(tmp_path, monkeypatch):
    spider = _make_spider(["broken", "ejeab"], tmp_path)

    def boom(_response, _page_id, _page_index):
        raise RuntimeError("timeline markup changed")

    monkeypatch.setattr(spider, "_parse_page_timeline", boom)

    timeline = _make_response(
        BASE_URL + "broken",
        _fixture("timeline_ok.html"),
        meta={"page_id": "broken", "page_index": 0},
    )
    outputs = _drain(spider.parse_page(timeline))
    # A parse anomaly on one page must not stop the remaining pages.
    assert len(outputs) == 1
    assert outputs[0].url == BASE_URL + "ejeab"

    report = _load_report(spider)
    assert report["stop_reason"] is None
    outcome = report["outcomes"][0]
    assert outcome["page_id"] == "broken"
    assert outcome["page_url"] == BASE_URL + "broken"
    assert outcome["status"] == PARSE_ERROR
    assert "RuntimeError" in outcome["detail"]
    assert "timeline markup changed" in outcome["detail"]


# --------------------------------------------------------------------------
# Report persistence
# --------------------------------------------------------------------------


def test_report_is_written_after_every_outcome(tmp_path):
    report_path = tmp_path / "incremental.json"
    spider = FbPageSpider(
        page_id="empty", outcome_output=str(report_path)
    )
    # Nothing written until the first outcome.
    assert not report_path.exists()

    timeline = _make_response(
        BASE_URL + "empty",
        _fixture("timeline_no_id.html"),
        meta={"page_id": "empty", "page_index": 0},
    )
    _drain(spider.parse_page(timeline))

    # The report is already on disk before the spider closes.
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["outcomes"]) == 1
    assert report["outcomes"][0]["status"] == NO_POST_ID


def test_missing_page_id_is_rejected():
    from scrapy.exceptions import CloseSpider

    with pytest.raises(CloseSpider):
        FbPageSpider(page_id=None)
