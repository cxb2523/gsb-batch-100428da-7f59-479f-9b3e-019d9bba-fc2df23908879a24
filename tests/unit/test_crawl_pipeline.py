"""Tests for the crawl pipeline: plugin output -> link discovery -> enqueue."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spider.plugin import Plugin, PluginManager
from spider.spider import Spider


CONFIG = {
    'threads': 4,
    'rate_limit': 0,
    'timeout': 10,
    'user_agent': 'TestBot/1.0',
}


class ExpandingPlugin(Plugin):
    """Simulates a dynamic-rendering plugin that returns a fuller HTML page."""

    def __init__(self, html: str):
        self.html = html

    async def process(self, url: str, content: str) -> str:
        return self.html


class BoomPlugin(Plugin):
    """Plugin that always raises."""

    async def process(self, url: str, content: str) -> str:
        raise RuntimeError("render failed")


class LinkRecordingPlugin(Plugin):
    """Returns well-formed content with an extra link appended."""

    def __init__(self, link: str):
        self.link = link

    async def process(self, url: str, content: str) -> str:
        anchor = f'<a href="{self.link}">extra</a>'
        if "</body>" in content:
            return content.replace("</body>", f"{anchor}</body>")
        return f"<html><body>{content}{anchor}</body></html>"


def make_spider(start_url: str, plugins=None, config=None) -> Spider:
    manager = PluginManager()
    for plugin in plugins or []:
        manager.register(plugin)
    return Spider(start_url, dict(config or CONFIG), manager)


def queue_snapshot(spider: Spider):
    """Drain the queue (without marking done) and return its contents."""
    urls = []
    while not spider.to_visit.empty():
        urls.append(spider.to_visit.get_nowait())
        spider.to_visit.task_done()
    return urls


def drop_seed(spider: Spider):
    """Remove the start URL seeded in __init__ so tests can inspect only new work."""
    queue_snapshot(spider)


def mock_session_for(pages: dict):
    """Build a mock aiohttp session serving {url: html}."""
    session = MagicMock()

    def _get(url, headers=None):
        response = AsyncMock()
        body = pages.get(url)
        if body is None:
            response.status = 404
            response.headers = {'Content-Type': 'text/html'}
        else:
            response.status = 200
            response.headers = {'Content-Type': 'text/html'}
            response.text = AsyncMock(return_value=body)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=None)
        return context

    session.get = MagicMock(side_effect=_get)
    return session


class TestLinkDiscoveryUsesPluginOutput:
    """Links must be discovered from the post-plugin content."""

    @pytest.mark.asyncio
    async def test_new_links_from_dynamic_plugin_are_enqueued(self):
        raw = "<html><body>short</body></html>"
        rendered = (
            '<html><body><a href="/rendered-page">Rendered</a>'
            '<a href="http://example.com/other">Other</a></body></html>'
        )
        spider = make_spider(
            "http://example.com",
            plugins=[ExpandingPlugin(rendered)],
        )
        drop_seed(spider)
        session = mock_session_for({"http://example.com": raw})

        with patch('spider.spider.save_page') as mock_save:
            await spider.process_url(session, "http://example.com")

        saved_url, saved_content = mock_save.call_args[0]
        assert saved_url == "http://example.com"
        assert saved_content == rendered

        enqueued = queue_snapshot(spider)
        assert "http://example.com/rendered-page" in enqueued
        assert "http://example.com/other" in enqueued

    @pytest.mark.asyncio
    async def test_plugin_added_link_is_crawled_end_to_end(self):
        raw = "<html><body>shell</body></html>"
        spider = make_spider(
            "http://example.com",
            plugins=[LinkRecordingPlugin("/discovered")],
        )

        fetched = []

        async def fake_fetch(session, url):
            fetched.append(url)
            if url == "http://example.com":
                return raw
            return "<html><body>child</body></html>"

        spider.fetch = fake_fetch
        with patch('spider.spider.save_page'):
            await spider.crawl()

        # The link only exists after the plugin ran, yet it was crawled.
        assert "http://example.com/discovered" in fetched
        assert sorted(fetched) == [
            "http://example.com",
            "http://example.com/discovered",
        ]

    @pytest.mark.asyncio
    async def test_links_are_not_discovered_from_raw_content(self):
        """A link that exists only in raw content is irrelevant; the plugin output
        is authoritative. Here raw has a link that the plugin removes."""
        raw = '<html><body><a href="/stale">stale</a></body></html>'
        rendered = "<html><body>fully rendered, no links</body></html>"
        spider = make_spider(
            "http://example.com",
            plugins=[ExpandingPlugin(rendered)],
        )
        drop_seed(spider)
        session = mock_session_for({"http://example.com": raw})

        with patch('spider.spider.save_page'):
            await spider.process_url(session, "http://example.com")

        assert queue_snapshot(spider) == []


class TestPluginFailureFallback:
    """A failing plugin must not stop saving or link discovery, and must log."""

    @pytest.mark.asyncio
    async def test_original_content_saved_and_links_found_on_failure(self, caplog):
        raw = '<html><body><a href="/raw-link">raw</a></body></html>'
        spider = make_spider("http://example.com", plugins=[BoomPlugin()])
        session = mock_session_for({"http://example.com": raw})

        with patch('spider.spider.save_page') as mock_save, \
                caplog.at_level(logging.ERROR):
            await spider.process_url(session, "http://example.com")

        mock_save.assert_called_once_with("http://example.com", raw)
        assert "http://example.com/raw-link" in queue_snapshot(spider)

        # The failure is logged with both the plugin name and the URL.
        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "BoomPlugin" in message and "http://example.com" in message
            for message in messages
        ), messages

    @pytest.mark.asyncio
    async def test_plugin_manager_returns_original_content_on_error(self):
        manager = PluginManager()
        manager.register(BoomPlugin())
        manager.register(LinkRecordingPlugin("/after"))

        result = await manager.run_plugins("http://example.com", "<html>raw</html>")

        # Even though a later plugin also modified the content, the caller gets
        # the unprocessed original so nothing half-processed is saved.
        assert result == "<html>raw</html>"

    @pytest.mark.asyncio
    async def test_plugin_manager_error_log_names_plugin_and_url(self, caplog):
        manager = PluginManager()
        manager.register(BoomPlugin())

        with caplog.at_level(logging.ERROR):
            await manager.run_plugins("http://example.com/a", "<html>raw</html>")

        assert any(
            record.levelno == logging.ERROR
            and "BoomPlugin" in record.getMessage()
            and "http://example.com/a" in record.getMessage()
            for record in caplog.records
        ), [record.getMessage() for record in caplog.records]


class TestDeduplication:
    """The same URL is enqueued and crawled at most once."""

    @pytest.mark.asyncio
    async def test_duplicate_links_from_one_page_enqueue_once(self):
        raw = (
            '<html><body>'
            '<a href="/dup">1</a><a href="/dup">2</a>'
            '<a href="/dup/">3</a><a href="http://example.com/dup">4</a>'
            '</body></html>'
        )
        spider = make_spider("http://example.com")
        drop_seed(spider)
        session = mock_session_for({"http://example.com": raw})

        with patch('spider.spider.save_page'):
            await spider.process_url(session, "http://example.com")

        assert queue_snapshot(spider) == ["http://example.com/dup"]

    @pytest.mark.asyncio
    async def test_same_link_from_two_pages_enqueued_once(self):
        spider = make_spider("http://example.com")
        drop_seed(spider)
        spider._enqueue("http://example.com/a")
        spider._enqueue("http://example.com/a/")
        assert queue_snapshot(spider) == ["http://example.com/a"]

    @pytest.mark.asyncio
    async def test_concurrent_processing_fetches_url_once(self):
        spider = make_spider("http://example.com")
        calls = []

        async def fake_fetch(session, url):
            calls.append(url)
            await asyncio.sleep(0.02)
            return "<html><body>ok</body></html>"

        spider.fetch = fake_fetch
        session = MagicMock()

        with patch('spider.spider.save_page') as mock_save:
            await asyncio.gather(*(
                spider.process_url(session, "http://example.com/page/")
                for _ in range(5)
            ))

        assert calls == ["http://example.com/page"]
        assert mock_save.call_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_crawl_does_not_refetch_start_page(self):
        spider = make_spider("http://example.com/", config=CONFIG)
        calls = []

        async def fake_fetch(session, url):
            calls.append(url)
            await asyncio.sleep(0.01)
            if url == "http://example.com":
                return '<html><a href="http://example.com/">self</a></html>'
            return "<html>child</html>"

        spider.fetch = fake_fetch
        with patch('spider.spider.save_page'):
            await spider.crawl()

        assert calls.count("http://example.com") == 1
        assert len(calls) == len(set(calls))


class TestStartUrlNormalization:
    """The start URL and discovered links share one canonical form."""

    def test_start_url_normalized_like_links(self):
        spider = make_spider("http://example.com/")
        assert spider.start_url == "http://example.com"
        assert queue_snapshot(spider) == ["http://example.com"]

    @pytest.mark.asyncio
    async def test_start_page_not_requeued_by_self_link(self):
        raw = '<html><body><a href="http://example.com/">self</a></body></html>'
        spider = make_spider("http://example.com/")
        drop_seed(spider)
        session = mock_session_for({"http://example.com": raw})

        with patch('spider.spider.save_page') as mock_save:
            await spider.process_url(session, spider.start_url)

        mock_save.assert_called_once_with("http://example.com", raw)
        assert queue_snapshot(spider) == []

    @pytest.mark.asyncio
    async def test_visited_set_survives_normalization_differences(self):
        spider = make_spider("http://example.com")
        spider.visited.add("http://example.com/page")

        async def fake_fetch(session, url):  # must not be called
            raise AssertionError("already-visited URL was fetched again")

        spider.fetch = fake_fetch
        await spider.process_url(MagicMock(), "http://example.com/page/")
