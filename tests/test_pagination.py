import json

import pytest
from scrapy.http import Request, TextResponse

from shortvideocrawl.pagination import (
    CONTINUE,
    COUNT_REACHED,
    CURSOR_HAS_MORE,
    CURSOR_NUMERIC,
    EMPTY_PAGE,
    INVALID_CURSOR,
    MISSING,
    NO_MORE,
    REPEATED_CURSOR,
    decide_next_page,
)
from shortvideocrawl.spiders.haokan import HaokanSpider
from shortvideocrawl.spiders.ixigua import IxiguaSpider
from shortvideocrawl.spiders.kuaishou import KuaishouSpider
from shortvideocrawl.spiders.quanmin import QuanminSpider


def make_response(spider, payload, page, url="https://example.test/search"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    request = Request(url, meta={"page": page})
    return TextResponse(url=url, body=body, encoding="utf-8", request=request)


def collect_results(spider, response, parser="parse"):
    return list(getattr(spider, parser)(response))


def is_request(result):
    return isinstance(result, Request)


# ---------------------------------------------------------------- helper unit


@pytest.mark.parametrize("cursor", ["no_more", "NO_MORE", "", "   ", None])
def test_terminal_markers_stop_without_cursor(cursor):
    decision = decide_next_page(
        cursor, current_page=3, entries_found=True, cursor_kind=CURSOR_NUMERIC
    )
    assert decision.reason == NO_MORE
    assert decision.next_page is None


@pytest.mark.parametrize("cursor", ["abc", "1.5", " 2x", [], {}, True, -1, MISSING])
def test_malformed_numeric_cursor_is_invalid(cursor):
    decision = decide_next_page(
        cursor, current_page=3, entries_found=True, cursor_kind=CURSOR_NUMERIC
    )
    assert decision.reason == INVALID_CURSOR
    assert decision.next_page is None


def test_numeric_cursor_advancing_continues():
    decision = decide_next_page(
        "40",
        current_page=20,
        entries_found=True,
        previous_cursor=20,
        cursor_kind=CURSOR_NUMERIC,
        count_allows_next=lambda page: page * 20 < 1000,
    )
    assert decision.reason == CONTINUE
    assert decision.next_page == 40


def test_repeated_numeric_cursor_stops():
    decision = decide_next_page(
        "20",
        current_page=20,
        entries_found=True,
        previous_cursor=20,
        cursor_kind=CURSOR_NUMERIC,
    )
    assert decision.reason == REPEATED_CURSOR


def test_backwards_numeric_cursor_stops():
    decision = decide_next_page(
        "10",
        current_page=20,
        entries_found=True,
        previous_cursor=20,
        cursor_kind=CURSOR_NUMERIC,
    )
    assert decision.reason == REPEATED_CURSOR


def test_count_limit_stops_numeric():
    decision = decide_next_page(
        "40",
        current_page=20,
        entries_found=True,
        previous_cursor=20,
        cursor_kind=CURSOR_NUMERIC,
        count_allows_next=lambda page: page * 20 < 40,
    )
    assert decision.reason == COUNT_REACHED


def test_empty_page_stops():
    decision = decide_next_page(
        "40", current_page=20, entries_found=False, cursor_kind=CURSOR_NUMERIC
    )
    assert decision.reason == EMPTY_PAGE


@pytest.mark.parametrize("flag", [0, False, None, "", "no_more"])
def test_has_more_terminal_flags(flag):
    decision = decide_next_page(
        flag, current_page=0, entries_found=True, cursor_kind=CURSOR_HAS_MORE
    )
    assert decision.reason == NO_MORE


@pytest.mark.parametrize("flag", ["1", "true", [], {}, MISSING, 2.0])
def test_has_more_malformed_flags(flag):
    decision = decide_next_page(
        flag, current_page=0, entries_found=True, cursor_kind=CURSOR_HAS_MORE
    )
    assert decision.reason == INVALID_CURSOR


def test_has_more_true_advances_page_number():
    decision = decide_next_page(
        1,
        current_page=2,
        entries_found=True,
        cursor_kind=CURSOR_HAS_MORE,
        count_allows_next=lambda page: page * 10 < 100,
    )
    assert decision.reason == CONTINUE
    assert decision.next_page == 3


def test_count_callback_exception_does_not_raise():
    def explode(_page):
        raise ValueError("bad count")

    decision = decide_next_page(
        1,
        current_page=0,
        entries_found=True,
        cursor_kind=CURSOR_HAS_MORE,
        count_allows_next=explode,
    )
    assert decision.reason == COUNT_REACHED


# ------------------------------------------------------------------- kuaishou


def kuaishou_payload(pcursor, feeds=None):
    if feeds is None:
        feeds = [
            {
                "photo": {
                    "id": "video-1",
                    "photoUrl": "https://cdn.example.test/1.mp4",
                }
            },
            {"photo": {"id": "missing-url"}},
            {"not_photo": True},
            "junk",
        ]
    return {"data": {"visionSearchPhoto": {"feeds": feeds, "pcursor": pcursor}}}


def test_kuaishou_keeps_parsable_items_and_stops_on_no_more(caplog):
    spider = KuaishouSpider(count=100)
    response = make_response(spider, kuaishou_payload("no_more"), page=0)
    results = collect_results(spider, response)

    items = [r for r in results if not is_request(r)]
    requests = [r for r in results if is_request(r)]
    assert [item["id"] for item in items] == ["video-1"]
    assert requests == []
    assert "site=kuaishou" in caplog.text
    assert "reason=no_more" in caplog.text
    assert "page=0" in caplog.text


@pytest.mark.parametrize("pcursor", [None, "", "abc", "0"])
def test_kuaishou_bad_cursor_does_not_raise(pcursor, caplog):
    spider = KuaishouSpider(count=100)
    response = make_response(spider, kuaishou_payload(pcursor), page=0)
    results = collect_results(spider, response)

    assert [r["id"] for r in results if not is_request(r)] == ["video-1"]
    assert [r for r in results if is_request(r)] == []
    assert "site=kuaishou" in caplog.text


def test_kuaishou_invalid_cursor_reason_is_logged(caplog):
    spider = KuaishouSpider(count=100)
    collect_results(spider, make_response(spider, kuaishou_payload("abc"), page=0))
    assert "reason=invalid_cursor" in caplog.text


def test_kuaishou_repeated_cursor_reason_is_logged(caplog):
    spider = KuaishouSpider(count=100)
    collect_results(spider, make_response(spider, kuaishou_payload("20"), page=20))
    assert "reason=repeated_cursor" in caplog.text


def test_kuaishou_missing_feeds_field_does_not_raise(caplog):
    spider = KuaishouSpider(count=100)
    payload = {"data": {"visionSearchPhoto": {"pcursor": 20}}}
    results = collect_results(spider, make_response(spider, payload, page=0))
    assert results == []
    assert "reason=empty_page" in caplog.text


def test_kuaishou_count_reached_reason(caplog):
    spider = KuaishouSpider(count=20)
    results = collect_results(
        spider, make_response(spider, kuaishou_payload("40"), page=20)
    )
    assert [r for r in results if is_request(r)] == []
    assert "reason=count_reached" in caplog.text


def test_kuaishou_continues_with_advancing_cursor():
    spider = KuaishouSpider(count=100)
    results = collect_results(
        spider, make_response(spider, kuaishou_payload("1"), page=0)
    )
    requests = [r for r in results if is_request(r)]
    assert len(requests) == 1
    assert json.loads(requests[0].body)["variables"]["pcursor"] == "1"


def test_kuaishou_empty_page_stops(caplog):
    spider = KuaishouSpider(count=100)
    results = collect_results(
        spider, make_response(spider, kuaishou_payload("20", feeds=[]), page=0)
    )
    assert [r for r in results if is_request(r)] == []
    assert "reason=empty_page" in caplog.text


# -------------------------------------------------------------------- quanmin


def quanmin_payload(has_more, videos=None):
    if videos is None:
        videos = [
            {"vid": "qm-1", "play_url": "https://cdn.example.test/qm1.mp4"},
            {"vid": "qm-2"},
            {"play_url": "https://cdn.example.test/noid.mp4"},
            {"vid": "", "play_url": "https://cdn.example.test/empty.mp4"},
            "junk",
        ]
    return {"data": {"list": {"video_list": videos, "has_more": has_more}}}


def test_quanmin_keeps_parsable_items_and_stops_on_zero(caplog):
    spider = QuanminSpider(count=100)
    results = collect_results(spider, make_response(spider, quanmin_payload(0), page=0))
    items = [r for r in results if not is_request(r)]
    assert [item["id"] for item in items] == ["qm-1"]
    assert [r for r in results if is_request(r)] == []
    assert "site=quanmin" in caplog.text
    assert "reason=no_more" in caplog.text


def test_quanmin_has_more_string_is_invalid(caplog):
    spider = QuanminSpider(count=100)
    results = collect_results(
        spider, make_response(spider, quanmin_payload("1"), page=0)
    )
    assert [r["id"] for r in results if not is_request(r)] == ["qm-1"]
    assert [r for r in results if is_request(r)] == []
    assert "reason=invalid_cursor" in caplog.text


def test_quanmin_missing_list_does_not_raise(caplog):
    spider = QuanminSpider(count=100)
    results = collect_results(spider, make_response(spider, {"data": {}}, page=0))
    assert results == []
    assert "reason=empty_page" in caplog.text


def test_quanmin_continues_with_true_flag():
    spider = QuanminSpider(count=100)
    results = collect_results(
        spider, make_response(spider, quanmin_payload(True), page=0)
    )
    requests = [r for r in results if is_request(r)]
    assert len(requests) == 1
    assert "pn=1" in requests[0].url


def test_quanmin_count_limit_preserved(caplog):
    spider = QuanminSpider(count=10)
    results = collect_results(spider, make_response(spider, quanmin_payload(1), page=0))
    assert [r for r in results if is_request(r)] == []
    assert "reason=count_reached" in caplog.text


# --------------------------------------------------------------------- haokan


def haokan_payload(has_more, videos=None):
    if videos is None:
        videos = [
            {"vid": "hk-1"},
            {"title": "no vid"},
            {"vid": ""},
            "junk",
        ]
    return {"data": {"list": videos, "has_more": has_more}}


def test_haokan_keeps_parsable_entries_and_stops_on_zero(caplog):
    spider = HaokanSpider(count=100)
    results = collect_results(
        spider, make_response(spider, haokan_payload(0), page=1), parser="parse_search"
    )
    detail_requests = [r for r in results if is_request(r)]
    assert len(detail_requests) == 1
    assert "vid=hk-1" in detail_requests[0].url
    assert "site=haokan" in caplog.text
    assert "reason=no_more" in caplog.text
    assert "page=1" in caplog.text


@pytest.mark.parametrize("flag", [None, "", "abc", MISSING])
def test_haokan_missing_or_bad_flag_stops(flag, caplog):
    spider = HaokanSpider(count=100)
    payload = {"data": {"list": [{"vid": "hk-1"}]}}
    if flag is not MISSING:
        payload["data"]["has_more"] = flag
    results = collect_results(
        spider, make_response(spider, payload, page=1), parser="parse_search"
    )
    search_requests = [r for r in results if is_request(r) and "ui-search" in r.url]
    assert search_requests == []
    assert caplog.text.count("site=haokan") >= 1


def test_haokan_missing_list_does_not_raise(caplog):
    spider = HaokanSpider(count=100)
    results = collect_results(
        spider, make_response(spider, {"data": {}}, page=1), parser="parse_search"
    )
    assert results == []
    assert "reason=empty_page" in caplog.text


def test_haokan_count_limit_preserved(caplog):
    spider = HaokanSpider(count=10)
    results = collect_results(
        spider, make_response(spider, haokan_payload(1), page=1), parser="parse_search"
    )
    assert [r for r in results if is_request(r) and "ui-search" in r.url] == []
    assert "reason=count_reached" in caplog.text


# --------------------------------------------------------------------- ixigua


def ixigua_payload(has_more, videos=None):
    if videos is None:
        videos = [
            {"data": {"group_id": "xg-1"}},
            {"data": {}},
            {"data": {"group_id": ""}},
            {"not_data": True},
            "junk",
        ]
    return {"data": {"data": videos, "has_more": has_more}}


def test_ixigua_keeps_parsable_entries_and_stops_on_false(caplog):
    spider = IxiguaSpider(count=100)
    results = collect_results(
        spider,
        make_response(spider, ixigua_payload(False), page=0),
        parser="parse_search",
    )
    detail_requests = [r for r in results if is_request(r)]
    assert len(detail_requests) == 1
    assert "xg-1" in detail_requests[0].url
    assert "site=ixigua" in caplog.text
    assert "reason=no_more" in caplog.text


def test_ixigua_bad_flag_stops_without_raising(caplog):
    spider = IxiguaSpider(count=100)
    results = collect_results(
        spider,
        make_response(spider, ixigua_payload("maybe"), page=0),
        parser="parse_search",
    )
    search_requests = [r for r in results if is_request(r) and "searchv2" in r.url]
    assert search_requests == []
    assert "reason=invalid_cursor" in caplog.text


def test_ixigua_continues_when_more_available():
    spider = IxiguaSpider(count=100)
    results = collect_results(
        spider,
        make_response(spider, ixigua_payload(True), page=0),
        parser="parse_search",
    )
    search_requests = [r for r in results if is_request(r) and "searchv2" in r.url]
    assert len(search_requests) == 1
    assert "/10?" in search_requests[0].url


def test_ixigua_missing_data_does_not_raise(caplog):
    spider = IxiguaSpider(count=100)
    results = collect_results(
        spider, make_response(spider, {}, page=0), parser="parse_search"
    )
    assert results == []
    assert "reason=empty_page" in caplog.text


def test_invalid_json_body_does_not_raise(caplog):
    for spider, parser in (
        (KuaishouSpider(count=100), "parse"),
        (QuanminSpider(count=100), "parse"),
        (HaokanSpider(count=100), "parse_search"),
        (IxiguaSpider(count=100), "parse_search"),
    ):
        parse = getattr(spider, parser)
        results = list(parse(make_response(spider, b"<html>oops</html>", page=0)))
        assert results == []
    assert "site=kuaishou" in caplog.text
    assert "site=quanmin" in caplog.text
    assert "site=haokan" in caplog.text
    assert "site=ixigua" in caplog.text
