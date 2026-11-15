# -*- coding: utf-8 -*-
"""Importable support objects used by the end-to-end crawl test.

Scrapy resolves ``DOWNLOAD_HANDLERS`` / ``ITEM_PIPELINES`` entries by dotted
path, so these live in a real module rather than inside the test file.  The
test configures them through the module level ``PAGES`` / ``COLLECTION``
attributes.

``tests`` is importable as a namespace package because ``conftest.py`` puts the
repository root on ``sys.path``.
"""

from __future__ import annotations

from scrapy.http import HtmlResponse

from olx_scraper.pipelines import MongoDBPipeline

from fakes import FakeCollection

PAGES: dict = {}
COLLECTION = FakeCollection()


class CannedDownloadHandler:
    """Serves pages from ``tests.support.PAGES``; unknown URLs return 404."""

    lazy = False

    async def download_request(self, request):
        page = PAGES.get(request.url)
        if page is None:
            return HtmlResponse(
                url=request.url,
                body=b"<html><body>not found</body></html>",
                status=404,
                request=request,
            )
        return HtmlResponse(
            url=request.url,
            body=page.encode("utf-8"),
            encoding="utf-8",
            request=request,
        )

    async def close(self):
        return None


class InjectedCollectionPipeline(MongoDBPipeline):
    """MongoDBPipeline bound to ``tests.support.COLLECTION``."""

    @classmethod
    def from_crawler(cls, crawler):
        return cls(COLLECTION, spider=crawler.spider)
