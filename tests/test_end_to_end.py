# -*- coding: utf-8 -*-
"""End-to-end crawl through the real Scrapy engine.

Canned pages are served by an in-process download handler and items go through
a MongoDBPipeline bound to an in-memory fake collection.  This exercises the
whole chain (list page -> detail pages -> item pipeline) the same way a real
crawl does.
"""

from __future__ import annotations

import asyncio
import warnings

import pytest

from scrapy.utils.test import AsyncCrawlerRunner

from olx_scraper.spiders.scrape_olx import ScraperSpider

import tests.support as support
from fakes import FakeCollection
from fixtures import (
    DETAIL_FULL,
    DETAIL_MISSING_DESCRIPTION,
    DETAIL_MISSING_PHONE,
    LIST_PAGE_BLOCKED,
    LIST_PAGE_EMPTY,
    LIST_PAGE_INVALID,
    LIST_PAGE_WITH_ADS,
)

LIST_URL = "https://www.olx.com/lahore/q-iphone"

BASE_SETTINGS = {
    "TWISTED_REACTOR_ENABLED": False,
    "ITEM_PIPELINES": {"tests.support.InjectedCollectionPipeline": 300},
    "DOWNLOAD_HANDLERS": {"http": "tests.support.CannedDownloadHandler",
                          "https": "tests.support.CannedDownloadHandler"},
    "LOG_ENABLED": False,
    "TELNETCONSOLE_ENABLED": False,
    "ROBOTSTXT_OBEY": False,
}


def run_crawl(pages, minimumprice="25000"):
    """Run one fully offline crawl and return (stats, spider, collection)."""

    collection = FakeCollection()
    support.PAGES.clear()
    support.PAGES.update(pages)
    support.COLLECTION = collection

    runner = AsyncCrawlerRunner(BASE_SETTINGS)
    crawler = runner.create_crawler(ScraperSpider)

    async def go():
        await runner.crawl(crawler)
        await runner.join()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.run(go())

    return crawler.stats.get_stats(), crawler.spider, collection


def standard_pages():
    return {
        "https://www.olx.com/": "<html><body>home</body></html>",
        LIST_URL: LIST_PAGE_WITH_ADS,
        "https://www.olx.com/item/iphone-11-111": DETAIL_FULL,
        "https://www.olx.com/item/iphone-13-333": DETAIL_MISSING_DESCRIPTION,
        "https://www.olx.com/item/iphone-14-555": DETAIL_MISSING_PHONE,
    }


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_full_crawl_persists_good_ads_and_degrades_missing_fields():
    stats, spider, collection = run_crawl(standard_pages())

    # Three ads survive (one unparseable price, one below minimum).
    assert len(collection.documents) == 3
    assert stats["olx/ads/seen"] == 5
    assert stats["olx/ads/skipped"] == 1
    assert stats["olx/ads/skip/price_unparseable"] == 1
    assert stats["olx/ads/filtered_below_min_price"] == 1
    assert stats["olx/items/inserted"] == 3
    assert stats["olx/items/write_failed"] == 0
    assert stats["olx/detail/missing_description"] == 1
    assert stats["olx/detail/missing_phone"] == 1
    assert stats["olx/crawl/status"] == "success"
    assert stats["finish_reason"] == "finished"

    by_url = {d["url"]: d for d in collection.documents}
    assert set(by_url) == {
        "https://www.olx.com/item/iphone-11-111",
        "https://www.olx.com/item/iphone-13-333",
        "https://www.olx.com/item/iphone-14-555",
    }
    degraded = by_url["https://www.olx.com/item/iphone-14-555"]
    assert degraded["phone_number"] == "Not available"
    assert degraded["phone_missing"] is True
    assert degraded["description"] == "Second hand iPhone, works perfectly."

    no_description = by_url["https://www.olx.com/item/iphone-13-333"]
    assert no_description["description"] == "Not available"
    assert no_description["description_missing"] is True


def test_empty_list_page_finishes_successfully_with_zero_items():
    stats, spider, collection = run_crawl(
        {"https://www.olx.com/": "<html></html>", LIST_URL: LIST_PAGE_EMPTY}
    )
    assert collection.documents == []
    assert stats["olx/crawl/status"] == "success"
    assert stats["olx/list_page/empty"] is True
    assert stats["olx/ads/seen"] == 0
    assert stats["finish_reason"] == "finished"


def test_invalid_list_page_ends_the_crawl_as_a_failure():
    stats, spider, collection = run_crawl(
        {"https://www.olx.com/": "<html></html>", LIST_URL: LIST_PAGE_INVALID}
    )
    assert collection.documents == []
    assert stats["olx/crawl/status"] == "failed"
    assert stats["olx/crawl/failure_reason"] == "list_page_invalid"
    assert stats["finish_reason"] == "list_page_invalid"
    assert stats["olx/list_page/empty"] is False


def test_blocked_list_page_is_reported_as_failure():
    stats, spider, collection = run_crawl(
        {"https://www.olx.com/": "<html></html>", LIST_URL: LIST_PAGE_BLOCKED}
    )
    assert stats["olx/crawl/status"] == "failed"
    assert stats["finish_reason"] == "list_page_invalid"


def test_duplicate_detail_link_is_updated_not_duplicated():
    """The same ad listed twice must produce a single updated document.

    Two identical requests are collapsed by Scrapy's dupefilter, so the
    duplicate is exercised through the pipeline directly: the second write to
    the same ``url`` replaces the first document instead of inserting a second
    one.
    """

    all_list = LIST_PAGE_WITH_ADS.replace(
        "/item/iphone-13-333", "/item/iphone-11-111"
    ).replace("/item/iphone-14-555", "/item/iphone-11-111")
    pages = standard_pages()
    pages[LIST_URL] = all_list

    stats, spider, collection = run_crawl(pages)

    urls = [d["url"] for d in collection.documents]
    assert urls.count("https://www.olx.com/item/iphone-11-111") == 1
    assert stats["olx/ads/yielded"] == 1
    assert stats["olx/items/inserted"] == 1


def test_repeat_run_updates_existing_documents():
    """A second crawl over the same ads updates rows instead of duplicating."""

    collection = FakeCollection()

    async def crawl_once(pages):
        support.PAGES.clear()
        support.PAGES.update(pages)
        support.COLLECTION = collection
        runner = AsyncCrawlerRunner(BASE_SETTINGS)
        crawler = runner.create_crawler(ScraperSpider)
        await runner.crawl(crawler)
        await runner.join()
        return crawler.stats.get_stats()

    async def go():
        first = await crawl_once(standard_pages())
        second = await crawl_once(standard_pages())
        return first, second

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        first, second = asyncio.run(go())

    assert first["olx/items/inserted"] == 3
    assert second["olx/items/updated"] == 3
    assert second["olx/items/inserted"] == 0
    assert len(collection.documents) == 3


def test_failing_write_does_not_lose_other_documents():
    pages = standard_pages()
    pages["https://www.olx.com/item/iphone-13-333"] = DETAIL_FULL

    # Run manually so the failing collection is installed before the crawl.
    flaky = FakeCollection(fail_on_urls={"https://www.olx.com/item/iphone-13-333"})
    support.PAGES.clear()
    support.PAGES.update(pages)
    support.COLLECTION = flaky

    runner = AsyncCrawlerRunner(BASE_SETTINGS)
    crawler = runner.create_crawler(ScraperSpider)

    async def go():
        await runner.crawl(crawler)
        await runner.join()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.run(go())

    stats = crawler.stats.get_stats()
    assert stats["olx/items/write_failed"] == 1
    assert stats["olx/items/written"] == 2
    assert len(flaky.documents) == 2
