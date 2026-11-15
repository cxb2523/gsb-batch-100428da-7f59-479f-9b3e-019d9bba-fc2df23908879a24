# -*- coding: utf-8 -*-

# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: http://doc.scrapy.org/en/latest/topics/item-pipeline.html

"""MongoDB persistence for OLX advertisements.

Behaviour:

* the advertisement's detail link (``url``) is the deduplication key; the same
  ad seen twice updates the existing document instead of inserting a duplicate;
* a failure while writing one document is logged, counted and swallowed so the
  rest of the batch keeps flowing;
* the collection is injectable (``MongoDBPipeline(collection=...)``) which
  makes it possible to verify write behaviour with an in-memory fake, without a
  real MongoDB instance.

``pymongo`` is imported lazily so the rest of the package (and its tests) do
not require the driver to be installed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MongoDBPipeline(object):
    """Upsert OLX items into MongoDB keyed by ``url``."""

    def __init__(self, collection, client=None, spider=None):
        self.collection = collection
        self.client = client
        self.spider = spider

    # ------------------------------------------------------------------ setup
    @classmethod
    def from_crawler(cls, crawler):
        settings = crawler.settings
        db_name = settings.get("MONGODB_DB") or ""
        collection_name = settings.get("MONGODB_COLLECTION") or ""
        if not db_name or not collection_name:
            raise ValueError(
                "MONGODB_DB and MONGODB_COLLECTION must be configured to use "
                "MongoDBPipeline (they are commented out in settings.py by "
                "default)."
            )

        from pymongo import MongoClient

        uri = settings.get("MONGODB_URI")
        if uri:
            client = MongoClient(uri)
        else:
            client = MongoClient(
                settings.get("MONGODB_SERVER", "localhost"),
                int(settings.get("MONGODB_PORT", 27017) or 27017),
            )
        return cls(client[db_name][collection_name], client=client, spider=crawler.spider)

    # ------------------------------------------------------------------ write
    def process_item(self, item, spider=None):
        spider = spider or self.spider
        document = dict(item)
        url = document.get("url")
        if not url:
            logger.error(
                "Skipping MongoDB write for item without a detail url: %r",
                document.get("title"),
            )
            self._record_failure(spider)
            return item

        try:
            inserted = self._upsert(url, document)
        except Exception:
            logger.exception(
                "MongoDB write failed for %s; continuing with the next item.", url
            )
            self._record_failure(spider)
            return item

        self._record_success(spider, inserted)
        return item

    def _upsert(self, url, document):
        """Write *document* keyed by *url*; return ``True`` when inserted."""
        replace_one = getattr(self.collection, "replace_one", None)
        if callable(replace_one):
            result = replace_one({"url": url}, document, upsert=True)
        else:
            update_one = getattr(self.collection, "update_one", None)
            if not callable(update_one):
                raise TypeError(
                    "collection must provide replace_one() or update_one(), got %r"
                    % type(self.collection)
                )
            result = update_one({"url": url}, {"$set": document}, upsert=True)
        return self._was_inserted(result)

    @staticmethod
    def _was_inserted(result):
        """Interpret a pymongo ``UpdateResult`` (or a dict lookalike)."""

        def get(name):
            value = getattr(result, name, None)
            if value is None and isinstance(result, dict):
                value = result.get(name)
            return value

        if get("upserted_id") is not None:
            return True
        # No upserted id: the filter matched an existing document.
        return not get("matched_count")

    # -------------------------------------------------------------- reporting
    @staticmethod
    def _outcome(spider):
        return getattr(spider, "outcome", None)

    def _record_success(self, spider, inserted):
        outcome = self._outcome(spider)
        if outcome is None:
            return
        if inserted:
            outcome.record_item_inserted()
        else:
            outcome.record_item_updated()

    def _record_failure(self, spider):
        outcome = self._outcome(spider)
        if outcome is not None:
            outcome.record_write_failed()

    # ------------------------------------------------------------- lifecycle
    def open_spider(self, spider=None):
        spider = spider or self.spider
        outcome = self._outcome(spider)
        if outcome is not None:
            outcome.record_pipeline_active()

    def close_spider(self, spider=None):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # pragma: no cover
                logger.exception("Error while closing the MongoDB client")
