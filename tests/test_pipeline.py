# -*- coding: utf-8 -*-
"""Pipeline tests against an injectable in-memory collection."""

from olx_scraper.items import OlxScraperItem
from olx_scraper.outcome import CrawlOutcome
from olx_scraper.pipelines import MongoDBPipeline

from fakes import ExplodingCollection, FakeCollection


class FakeSpider:
    name = "scrape_olx"

    def __init__(self):
        self.outcome = CrawlOutcome()


def make_item(url, title="iPhone", price="Rs 50,000", price_value=50000):
    return OlxScraperItem(
        title=title,
        phone_number="+923001234567",
        date="Today",
        price=price,
        price_value=price_value,
        url=url,
        description="Good condition",
        user="Ali",
        phone_missing=False,
        description_missing=False,
        seller_missing=False,
        date_missing=False,
    )


def test_first_write_inserts_second_write_updates_same_document():
    collection = FakeCollection()
    spider = FakeSpider()
    pipeline = MongoDBPipeline(collection)
    pipeline.open_spider(spider)

    item = make_item("https://www.olx.com/item/1")
    pipeline.process_item(item, spider)
    assert collection.count_documents() == 1

    # Same ad (same detail url) scraped again with a newer price.
    pipeline.process_item(
        make_item("https://www.olx.com/item/1", price="Rs 45,000", price_value=45000),
        spider,
    )
    assert collection.count_documents() == 1
    assert collection.documents[0]["price"] == "Rs 45,000"

    assert spider.outcome.items_inserted == 1
    assert spider.outcome.items_updated == 1
    assert spider.outcome.items_written == 2


def test_distinct_urls_produce_distinct_documents():
    collection = FakeCollection()
    spider = FakeSpider()
    pipeline = MongoDBPipeline(collection)
    pipeline.open_spider(spider)

    pipeline.process_item(make_item("https://www.olx.com/item/1"), spider)
    pipeline.process_item(make_item("https://www.olx.com/item/2"), spider)

    assert collection.count_documents() == 2
    assert spider.outcome.items_inserted == 2
    assert spider.outcome.items_updated == 0


def test_single_write_failure_does_not_stop_the_batch():
    collection = FakeCollection(fail_on_urls={"https://www.olx.com/item/bad"})
    spider = FakeSpider()
    pipeline = MongoDBPipeline(collection)
    pipeline.open_spider(spider)

    pipeline.process_item(make_item("https://www.olx.com/item/1"), spider)
    pipeline.process_item(make_item("https://www.olx.com/item/bad"), spider)
    pipeline.process_item(make_item("https://www.olx.com/item/3"), spider)

    # The two healthy items were persisted; the failing one was counted only.
    assert collection.urls == [
        "https://www.olx.com/item/1",
        "https://www.olx.com/item/3",
    ]
    assert spider.outcome.items_written == 2
    assert spider.outcome.items_write_failed == 1


def test_arbitrary_write_failure_by_call_index_is_isolated():
    collection = FakeCollection(fail_on_call={2})
    spider = FakeSpider()
    pipeline = MongoDBPipeline(collection)
    pipeline.open_spider(spider)

    for suffix in ("1", "2", "3", "4"):
        pipeline.process_item(
            make_item("https://www.olx.com/item/%s" % suffix), spider
        )

    assert len(collection.documents) == 3
    assert spider.outcome.items_written == 3
    assert spider.outcome.items_write_failed == 1


def test_pipeline_never_raises_when_collection_is_dead():
    collection = ExplodingCollection()
    spider = FakeSpider()
    pipeline = MongoDBPipeline(collection)
    pipeline.open_spider(spider)

    for suffix in ("1", "2", "3"):
        # Must not raise: a dead database cannot abort the crawl.
        pipeline.process_item(
            make_item("https://www.olx.com/item/%s" % suffix), spider
        )

    assert collection.write_calls == 3
    assert spider.outcome.items_written == 0
    assert spider.outcome.items_write_failed == 3


def test_item_without_url_is_counted_as_failure_not_written():
    collection = FakeCollection()
    spider = FakeSpider()
    pipeline = MongoDBPipeline(collection)

    item = make_item("https://www.olx.com/item/1")
    item["url"] = ""
    pipeline.process_item(item, spider)

    assert collection.count_documents() == 0
    assert spider.outcome.items_write_failed == 1


def test_pipeline_without_outcome_still_works():
    collection = FakeCollection()
    pipeline = MongoDBPipeline(collection)
    pipeline.process_item(make_item("https://www.olx.com/item/1"), object())


def test_update_one_only_collection_is_supported():
    class UpdateOnlyCollection(FakeCollection):
        replace_one = None  # force the update_one path

        def update_one(self, filter, update, upsert=False):
            return self._write("update_one", filter, update.get("$set", {}), upsert)

    collection = UpdateOnlyCollection()
    spider = FakeSpider()
    pipeline = MongoDBPipeline(collection)

    item = make_item("https://www.olx.com/item/1")
    result = pipeline.process_item(item, spider)
    assert result["url"] == item["url"]
    assert collection.urls == ["https://www.olx.com/item/1"]
    assert spider.outcome.items_inserted == 1
