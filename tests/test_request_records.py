#!/usr/bin/env python
"""
Acceptance tests for the per-request outcome records.

All requests are served by a local aiohttp server, no external site is used:

- ``/normal``      always returns 200
- ``/flaky``       returns 500 twice then 200
- ``/always-fail`` always returns 500
- ``/page/1``      returns 200 with a link to ``/page/2``
- ``/page/2``      returns 200
"""

import asyncio

from aiohttp import web

from ruia import Request, Spider
from ruia.record import RequestOutcome

WORKERS = 2


class LocalServer:
    """A local HTTP server serving the pages used by the tests"""

    def __init__(self):
        self.runner = None
        self.port = None
        self.hits = {}

    async def start(self):
        app = web.Application()
        app.router.add_get("/normal", self.normal)
        app.router.add_get("/flaky", self.flaky)
        app.router.add_get("/always-fail", self.always_fail)
        app.router.add_get("/page/1", self.page_one)
        app.router.add_get("/page/2", self.page_two)
        app.router.add_get("/chain/{page}", self.chain)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        if self.runner is not None:
            await self.runner.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    async def normal(self, request):
        self.hits["normal"] = self.hits.get("normal", 0) + 1
        return web.Response(text="<html><body>normal</body></html>")

    async def flaky(self, request):
        count = self.hits.get("flaky", 0) + 1
        self.hits["flaky"] = count
        if count <= 2:
            return web.Response(status=500, text="boom")
        return web.Response(text="<html><body>ok</body></html>")

    async def always_fail(self, request):
        self.hits["always-fail"] = self.hits.get("always-fail", 0) + 1
        return web.Response(status=500, text="always broken")

    async def page_one(self, request):
        self.hits["page/1"] = self.hits.get("page/1", 0) + 1
        body = '<html><body><a href="/page/2">next</a></body></html>'
        return web.Response(text=body)

    async def page_two(self, request):
        self.hits["page/2"] = self.hits.get("page/2", 0) + 1
        return web.Response(text="<html><body>last</body></html>")

    async def chain(self, request):
        page = int(request.match_info["page"])
        self.hits[f"chain/{page}"] = self.hits.get(f"chain/{page}", 0) + 1
        return web.Response(text=f"<html><body>page {page}</body></html>")


def run_scenario(scenario):
    """
    Run ``scenario(server)`` on a fresh event loop together with the local
    HTTP server, and return the scenario result.
    """

    async def main():
        server = LocalServer()
        await server.start()
        try:
            return await scenario(server)
        finally:
            await server.stop()

    loop = asyncio.new_event_loop()
    previous = None
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        pass
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(main())
    finally:
        loop.close()
        asyncio.set_event_loop(previous)


def start_spider(spider_class, record_requests=True):
    """Start ``spider_class`` on the current running loop"""
    loop = asyncio.get_event_loop()
    return spider_class.async_start(loop=loop, record_requests=record_requests)


def test_normal_page_is_success():
    async def scenario(server):
        class NormalSpider(Spider):
            start_urls = [server.url("/normal")]

            async def parse(self, response):
                assert response.status == 200

        return server.url("/normal"), await start_spider(NormalSpider)

    url, spider_ins = run_scenario(scenario)

    record = spider_ins.get_request_record(url)
    assert record is not None
    assert record.outcome == RequestOutcome.SUCCESS
    assert record.attempts == 1
    assert record.status == 200
    assert record.error is None
    assert record.method == "GET"
    assert spider_ins.success_counts == 1
    assert spider_ins.failed_counts == 0
    assert spider_ins.get_outcome_counts()[RequestOutcome.SUCCESS] == 1


def test_flaky_page_is_retried_success():
    async def scenario(server):
        class FlakySpider(Spider):
            start_urls = [server.url("/flaky")]
            request_config = {"RETRIES": 3, "TIMEOUT": 10}

            async def parse(self, response):
                pass

        return server, await start_spider(FlakySpider)

    server, spider_ins = run_scenario(scenario)

    record = spider_ins.get_request_record(server.url("/flaky"))
    assert record is not None
    assert record.outcome == RequestOutcome.RETRIED_SUCCESS
    assert record.attempts > 1
    assert record.status == 200
    # 2 failed tries then a successful one
    assert record.attempts == 3
    assert server.hits["flaky"] == 3
    assert spider_ins.success_counts == 1
    assert spider_ins.failed_counts == 0


def test_always_failing_page_is_failed():
    async def scenario(server):
        class FailingSpider(Spider):
            start_urls = [server.url("/always-fail")]
            request_config = {"RETRIES": 2, "TIMEOUT": 10}

            async def parse(self, response):
                pass

        return server.url("/always-fail"), await start_spider(FailingSpider)

    url, spider_ins = run_scenario(scenario)

    record = spider_ins.get_request_record(url)
    assert record is not None
    assert record.outcome == RequestOutcome.FAILED
    assert record.error
    assert "500" in record.error
    assert record.status == -1
    assert record.attempts == 3  # 1 initial try + 2 retries
    assert spider_ins.failed_counts == 1
    assert spider_ins.success_counts == 0
    assert spider_ins.get_outcome_counts()[RequestOutcome.FAILED] == 1


def test_child_request_has_parent_url():
    async def scenario(server):
        class PaginationSpider(Spider):
            start_urls = [server.url("/page/1")]

            async def parse(self, response):
                if response.url.endswith("/page/1"):
                    yield Request(
                        url=server.url("/page/2"),
                        callback=self.parse_detail,
                    )

            async def parse_detail(self, response):
                pass

        return server, await start_spider(PaginationSpider)

    server, spider_ins = run_scenario(scenario)

    child = spider_ins.get_request_record(server.url("/page/2"))
    parent = spider_ins.get_request_record(server.url("/page/1"))
    assert child is not None and parent is not None
    assert child.parent_url == server.url("/page/1")
    assert parent.parent_url is None
    assert child.callback == "parse"
    assert child.request_callback == "parse_detail"
    assert child.outcome == RequestOutcome.SUCCESS


def test_pagination_chain_is_traceable():
    async def scenario(server):
        class ChainSpider(Spider):
            start_urls = [server.url("/chain/1")]

            async def parse(self, response):
                page = int(response.url.rsplit("/", 1)[-1])
                if page < 3:
                    yield Request(
                        url=server.url(f"/chain/{page + 1}"),
                        callback=self.parse,
                    )

        return server, await start_spider(ChainSpider)

    server, spider_ins = run_scenario(scenario)

    first = spider_ins.get_request_record(server.url("/chain/1"))
    second = spider_ins.get_request_record(server.url("/chain/2"))
    third = spider_ins.get_request_record(server.url("/chain/3"))
    assert first is not None and second is not None and third is not None
    assert first.parent_url is None
    assert second.parent_url == server.url("/chain/1")
    assert third.parent_url == server.url("/chain/2")
    assert all(record.outcome == RequestOutcome.SUCCESS for record in (first, second, third))
    assert len(spider_ins.get_request_records()) == 3


def test_outcome_counts_match_statistics():
    async def scenario(server):
        class MixedSpider(Spider):
            worker_numbers = WORKERS
            request_config = {"RETRIES": 2, "TIMEOUT": 10}

            async def parse(self, response):
                pass

        MixedSpider.start_urls = [
            server.url("/normal"),
            server.url("/flaky"),
            server.url("/always-fail"),
        ]
        return server, await start_spider(MixedSpider)

    server, spider_ins = run_scenario(scenario)

    counts = spider_ins.get_outcome_counts()
    assert counts[RequestOutcome.SUCCESS] == 1
    assert counts[RequestOutcome.RETRIED_SUCCESS] == 1
    assert counts[RequestOutcome.FAILED] == 1

    success_records = counts[RequestOutcome.SUCCESS] + counts[
        RequestOutcome.RETRIED_SUCCESS
    ]
    assert success_records == spider_ins.success_counts
    assert counts[RequestOutcome.FAILED] == spider_ins.failed_counts
    assert len(spider_ins.get_request_records()) == 3
    assert len(spider_ins.get_request_records(outcome=RequestOutcome.FAILED)) == 1
    assert len(spider_ins.get_request_records(url=server.url("/normal"))) == 1


def test_records_disabled_keeps_legacy_behaviour():
    async def scenario(server):
        class PlainSpider(Spider):
            start_urls = [server.url("/normal"), server.url("/always-fail")]
            request_config = {"RETRIES": 1, "TIMEOUT": 10}

            async def parse(self, response):
                pass

        return await start_spider(PlainSpider, record_requests=False)

    spider_ins = run_scenario(scenario)

    assert spider_ins.success_counts == 1
    assert spider_ins.failed_counts == 1
    assert len(spider_ins.get_request_records()) == 0
    assert spider_ins.get_outcome_counts() == {
        RequestOutcome.SUCCESS: 0,
        RequestOutcome.RETRIED_SUCCESS: 0,
        RequestOutcome.FAILED: 0,
    }


def test_retry_func_new_request_keeps_attempts():
    async def scenario(server):
        class RetryFuncSpider(Spider):
            start_urls = [server.url("/flaky")]

            async def parse(self, response):
                pass

        async def retry_func(request):
            # Return a brand new Request, the attempts must stay on the chain
            return Request(
                url=request.url,
                request_session=request.request_session,
                request_config=request.request_config,
            )

        RetryFuncSpider.request_config = {
            "RETRIES": 3,
            "TIMEOUT": 10,
            "RETRY_FUNC": retry_func,
        }
        return server, await start_spider(RetryFuncSpider)

    server, spider_ins = run_scenario(scenario)

    record = spider_ins.get_request_record(server.url("/flaky"))
    assert record is not None
    assert record.outcome == RequestOutcome.RETRIED_SUCCESS
    assert record.attempts == 3
    assert record.status == 200
    assert spider_ins.success_counts == 1
    assert spider_ins.failed_counts == 0


def test_concurrent_workers_record_independently():
    async def scenario(server):
        class ConcurrentSpider(Spider):
            worker_numbers = 4
            request_config = {"RETRIES": 2, "TIMEOUT": 10}

            async def parse(self, response):
                pass

        ConcurrentSpider.start_urls = [
            server.url("/normal"),
            server.url("/flaky"),
            server.url("/always-fail"),
            server.url("/page/2"),
        ]
        return server, await start_spider(ConcurrentSpider)

    server, spider_ins = run_scenario(scenario)

    counts = spider_ins.get_outcome_counts()
    assert counts[RequestOutcome.SUCCESS] == 2
    assert counts[RequestOutcome.RETRIED_SUCCESS] == 1
    assert counts[RequestOutcome.FAILED] == 1
    assert spider_ins.success_counts == 3
    assert spider_ins.failed_counts == 1

    assert spider_ins.get_request_record(server.url("/normal")).attempts == 1
    assert spider_ins.get_request_record(server.url("/page/2")).attempts == 1
    flaky = spider_ins.get_request_record(server.url("/flaky"))
    assert flaky.outcome == RequestOutcome.RETRIED_SUCCESS
    assert flaky.attempts == 3
    failing = spider_ins.get_request_record(server.url("/always-fail"))
    assert failing.outcome == RequestOutcome.FAILED
    assert failing.attempts == 3


def test_unconfigured_spider_keeps_statistics():
    """
    A spider which never opts in to the feature keeps the original statistics
    and produces no records.
    """

    async def scenario(server):
        class PlainSpider(Spider):
            start_urls = [server.url("/normal"), server.url("/always-fail")]
            request_config = {"RETRIES": 1, "TIMEOUT": 10}

            async def parse(self, response):
                pass

        # No ``record_requests`` argument and no class attribute
        loop = asyncio.get_event_loop()
        return await PlainSpider.async_start(loop=loop)

    spider_ins = run_scenario(scenario)

    assert spider_ins.record_requests is False
    assert spider_ins.success_counts == 1
    assert spider_ins.failed_counts == 1
    assert len(spider_ins.get_request_records()) == 0
    assert spider_ins.get_outcome_counts() == {
        RequestOutcome.SUCCESS: 0,
        RequestOutcome.RETRIED_SUCCESS: 0,
        RequestOutcome.FAILED: 0,
    }


def test_retry_error_is_failed():
    async def scenario(server):
        class ErrorSpider(Spider):
            start_urls = ["http://127.0.0.1:1/never"]
            request_config = {"RETRIES": 1, "TIMEOUT": 0.5}

            async def parse(self, response):
                pass

        return await start_spider(ErrorSpider)

    spider_ins = run_scenario(scenario)

    record = spider_ins.get_request_record("http://127.0.0.1:1/never")
    assert record is not None
    assert record.outcome == RequestOutcome.FAILED
    assert record.attempts >= 1
    assert record.error
    assert record.status == -1
