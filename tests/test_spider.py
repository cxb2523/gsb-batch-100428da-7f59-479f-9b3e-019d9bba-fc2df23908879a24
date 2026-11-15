# -*- coding: utf-8 -*-
"""Spider behaviour tests using local HTML samples (no network)."""

import pytest
from scrapy.exceptions import CloseSpider
from scrapy.http import Request

from olx_scraper.outcome import (
    SKIP_PRICE_UNPARSEABLE,
    STATUS_SUCCESS,
)
from olx_scraper.spiders.scrape_olx import (
    DATE_FALLBACK,
    DESCRIPTION_FALLBACK,
    PHONE_FALLBACK,
    SELLER_FALLBACK,
    ScraperSpider,
)
from fixtures import (
    BASE,
    DETAIL_FULL,
    DETAIL_MISSING_BOTH,
    DETAIL_MISSING_DESCRIPTION,
    DETAIL_MISSING_PHONE,
    LIST_PAGE_BLOCKED,
    LIST_PAGE_EMPTY,
    LIST_PAGE_INVALID,
    LIST_PAGE_WITH_ADS,
    detail_response,
    make_response,
)


@pytest.fixture
def spider(monkeypatch):
    """A spider with deterministic args.py configuration."""

    def fake_info(key):
        return {
            "city": "lahore",
            "product": "iphone",
            "minimumprice": "25000",
        }.get(key.lower(), "")

    monkeypatch.setattr(
        "olx_scraper.spiders.scrape_olx.load_info", lambda: fake_info
    )
    return ScraperSpider()


def _drain(result):
    return list(result) if result is not None else []


def test_list_page_with_bad_price_skips_only_that_ad(spider):
    response = make_response(BASE, LIST_PAGE_WITH_ADS)
    requests = _drain(spider.parse_products(response))

    assert len(requests) == 3  # 125000, 200000 and 350000 pass; 15000 filtered
    assert all(isinstance(r, Request) for r in requests)
    assert {r.meta["price_value"] for r in requests} == {125000, 200000, 350000}

    outcome = spider.outcome
    assert outcome.ads_seen == 5
    assert outcome.ads_skipped == 1
    assert outcome.skip_reasons[SKIP_PRICE_UNPARSEABLE] == 1
    assert outcome.ads_filtered_below_min_price == 1
    assert outcome.status == STATUS_SUCCESS
    # 3 50 000 uses spaces as separators; it must be understood, not skipped.
    assert [r.meta["price_value"] for r in requests if "555" in r.meta["url"]] == [
        350000
    ]


def test_minimum_price_filter_keeps_legacy_semantics(spider):
    spider._configure()
    assert spider.min_price == 25000

    response = make_response("https://www.olx.com/lahore/q-iphone", LIST_PAGE_WITH_ADS)
    requests = _drain(spider.parse_products(response))
    urls = [r.url for r in requests]
    assert "https://www.olx.com/item/cheap-444" not in urls


def test_missing_phone_and_description_use_explicit_fallbacks(spider):
    missing_phone = detail_response(
        "/item/x",
        DETAIL_MISSING_PHONE,
        meta={
            "title": "iPhone X",
            "price": "Rs 90,000",
            "price_value": 90000,
            "date": "Today",
        },
    )
    item = next(iter(spider.product_page(missing_phone)))
    assert item["phone_number"] == PHONE_FALLBACK
    assert item["phone_missing"] is True
    assert item["description"] == "Second hand iPhone, works perfectly."
    assert item["description_missing"] is False
    assert spider.outcome.detail_missing_phone == 1

    missing_description = detail_response(
        "/item/y",
        DETAIL_MISSING_DESCRIPTION,
        meta={
            "title": "iPhone 12",
            "price": "Rs 1,00,000",
            "price_value": 100000,
            "date": "Today",
        },
    )
    item = next(iter(spider.product_page(missing_description)))
    assert item["description"] == DESCRIPTION_FALLBACK
    assert item["description_missing"] is True
    assert item["phone_number"] == "+923217654321"
    assert spider.outcome.detail_missing_description == 1


def test_detail_page_missing_everything_still_yields_item(spider):
    response = detail_response(
        "/item/z",
        DETAIL_MISSING_BOTH,
        meta={"title": "iPhone", "price": "Rs 50,000", "price_value": 50000},
    )
    item = next(iter(spider.product_page(response)))
    assert item["phone_number"] == PHONE_FALLBACK
    assert item["description"] == DESCRIPTION_FALLBACK
    assert item["user"] == SELLER_FALLBACK
    assert item["date"] == DATE_FALLBACK
    assert item["phone_missing"] and item["description_missing"]
    assert item["seller_missing"] and item["date_missing"]


def test_full_detail_page_has_no_missing_flags(spider):
    response = detail_response(
        "/item/full",
        DETAIL_FULL,
        meta={
            "title": "iPhone 11",
            "price": "Rs 1,25,000",
            "price_value": 125000,
            "date": "Yesterday",
        },
    )
    item = next(iter(spider.product_page(response)))
    assert item["phone_number"] == "+923001234567"
    assert item["user"] == "Ali Khan"
    assert item["description"].startswith("Great phone")
    assert not any(
        item[flag]
        for flag in (
            "phone_missing",
            "description_missing",
            "seller_missing",
            "date_missing",
        )
    )


def test_empty_list_page_is_success_with_zero_ads(spider):
    response = make_response(BASE, LIST_PAGE_EMPTY)
    requests = _drain(spider.parse_products(response))
    assert requests == []
    assert spider.outcome.list_page_empty is True
    assert spider.outcome.status == STATUS_SUCCESS
    assert spider.outcome.failure_reason is None


def test_invalid_list_page_raises_explicit_failure(spider):
    response = make_response(BASE, LIST_PAGE_INVALID)
    with pytest.raises(CloseSpider) as excinfo:
        _drain(spider.parse_products(response))
    assert excinfo.value.reason == "list_page_invalid"
    assert spider.outcome.failure_reason == "list_page_invalid"
    assert spider.outcome.list_page_empty is False


def test_blocked_list_page_is_failure_not_empty_success(spider):
    response = make_response(BASE, LIST_PAGE_BLOCKED)
    with pytest.raises(CloseSpider):
        _drain(spider.parse_products(response))
    assert spider.outcome.failure_reason == "list_page_invalid"


def test_detail_parse_error_does_not_abort_other_ads(spider, monkeypatch):
    """An unexpected exception while parsing one detail page is isolated."""

    response = make_response(BASE, LIST_PAGE_WITH_ADS)
    _drain(spider.parse_products(response))

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(spider, "_build_item", boom)
    broken = detail_response("/item/boom", DETAIL_FULL, meta={})
    assert _drain(spider.product_page(broken)) == []
    assert spider.outcome.skip_reasons["detail_parse_error"] == 1
    assert spider.outcome.ads_skipped == 2  # 1 bad price + 1 broken detail


def test_detail_request_failure_is_counted_not_fatal(spider):
    class Failure:
        request = Request("https://www.olx.com/item/gone")
        value = RuntimeError("connection reset")

    spider.detail_request_failed(Failure())
    assert spider.outcome.skip_reasons["detail_request_failed"] == 1
    assert spider.outcome.status == STATUS_SUCCESS


def test_outcome_snapshot_is_json_serialisable(spider):
    response = make_response(BASE, LIST_PAGE_WITH_ADS)
    _drain(spider.parse_products(response))
    response = detail_response("/item/full", DETAIL_FULL, meta={"title": "t"})
    _drain(spider.product_page(response))

    import json

    snapshot = json.loads(spider.outcome.summary_json())
    assert snapshot["ads_seen"] == 5
    assert snapshot["ads_skipped"] == 1
    assert snapshot["ads_yielded"] >= 1
    assert snapshot["status"] == STATUS_SUCCESS


def test_list_url_keeps_args_configuration(spider):
    spider._configure()
    response = make_response("https://www.olx.com/", "<html></html>")
    requests = _drain(spider.parse(response))
    assert requests[0].url == "https://www.olx.com/lahore/q-iphone"


def test_start_requests_wraps_parse(spider):
    requests = _drain(spider.start_requests())
    assert requests and requests[0].callback == spider.parse


def test_list_request_failure_marks_crawl_failed(spider):
    class Failure:
        value = RuntimeError("dns failure")

    with pytest.raises(CloseSpider) as excinfo:
        spider.list_request_failed(Failure())

    assert excinfo.value.reason == "list_request_failed"
    assert spider.outcome.failure_reason == "list_request_failed"
    assert spider.outcome.status != STATUS_SUCCESS


def test_base_url_argument_relocates_start_urls():
    spider = ScraperSpider(base_url="http://127.0.0.1:9999/")
    assert spider.start_urls == ["http://127.0.0.1:9999/"]


def test_min_price_ignores_invalid_configuration(monkeypatch):
    def fake_info(key):
        return {"city": "lahore", "product": "iphone", "minimumprice": "abc"}.get(
            key.lower(), ""
        )

    monkeypatch.setattr("olx_scraper.spiders.scrape_olx.load_info", lambda: fake_info)
    spider = ScraperSpider()
    spider._configure()
    assert spider.min_price == 0


def test_missing_detail_url_is_skipped_and_counted(spider):
    html = """
    <html><body><table>
      <tr><td class="offer">
        <h3><span>No link here</span></h3>
        <p class="price"><strong>Rs 50,000</strong></p>
      </td></tr>
    </table></body></html>
    """
    response = make_response(BASE, html)
    assert _drain(spider.parse_products(response)) == []
    assert spider.outcome.skip_reasons["missing_detail_url"] == 1
    assert spider.outcome.ads_seen == 1
