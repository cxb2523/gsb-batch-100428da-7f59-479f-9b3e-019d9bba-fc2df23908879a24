# tests/test_addon_discovery_hook.py
#
# Acceptance tests for target discovery driven through the real mitmproxy
# addon entry point (MainAddon.response) instead of calling the crawler
# functions directly. A crafted HTML response is fed into a tflow flow so the
# whole plugin hook chain (scope check -> response parsing -> queueing) runs.
import asyncio

import pytest
from mitmproxy import http
from mitmproxy.test import tflow

try:
    from nightcrawler.addon import MainAddon
except ImportError:
    pytest.fail("Could not import MainAddon", pytrace=False)


def _build_flow(html: str, url: str = "http://test.com/index") -> http.HTTPFlow:
    """Builds an in-scope flow carrying a crafted HTML response."""
    flow = tflow.tflow(resp=True)
    flow.request = http.Request.make("GET", url)
    flow.response = http.Response.make(
        200,
        content=html.encode("utf-8"),
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    return flow


def _drain_queue(queue: "asyncio.Queue") -> list:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


@pytest.fixture
def real_addon():
    """A real MainAddon with fresh queues and no background workers running."""
    addon = MainAddon()
    addon.effective_scope = {"test.com"}
    # Attributes normally populated by configure() during addon startup.
    addon.user_agent = "Nightcrawler-Test-UA/1.0"
    # No passive scanner side effects are relevant to discovery; the response
    # hook schedules them as tasks that simply iterate over an empty list.
    addon.passive_scanners = []
    return addon


async def _flush_hook_tasks():
    # response() schedules passive checks via asyncio.create_task(); give them
    # a chance to run (and finish, with an empty scanner list) before asserts.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_response_hook_enqueues_two_forms_with_different_defaults(real_addon):
    """
    Acceptance (via the plugin response hook): two forms sharing the same
    action and parameter names but pre-filled with different default values
    must each produce exactly one scan_queue entry.
    """
    html = """
    <html><body>
      <form action="/switch" method="GET">
        <input type="hidden" name="mode" value="light">
      </form>
      <form action="/switch" method="GET">
        <input type="hidden" name="mode" value="dark">
      </form>
    </body></html>
    """
    real_addon.response(_build_flow(html))
    await _flush_hook_tasks()

    scan_entries = _drain_queue(real_addon.scan_queue)
    assert len(scan_entries) == 2
    assert all(entry["method"] == "GET" for entry in scan_entries)
    assert all(entry["url"] == "http://test.com/switch" for entry in scan_entries)
    assert sorted(entry["params"]["mode"] for entry in scan_entries) == [
        "dark",
        "light",
    ]
    # Nothing crawlable: the page contains no hyperlinks.
    assert real_addon.crawl_queue.empty()
    # Two distinct form signatures must have been recorded.
    assert sum(1 for s in real_addon.scanned_targets if s.startswith("form::")) == 2


@pytest.mark.asyncio
async def test_response_hook_keeps_link_and_form_targets_separate(real_addon):
    """
    Acceptance (via the plugin response hook): a parameterized link and a form
    on the same path with the same parameter name must land in crawl_queue and
    scan_queue respectively without one swallowing the other's signature.
    """
    html = """
    <html><body>
      <a href="/search?q=fromlink">Search link</a>
      <form action="/search" method="GET">
        <input name="q" value="fromform">
      </form>
    </body></html>
    """
    real_addon.response(_build_flow(html))
    await _flush_hook_tasks()

    crawl_urls = _drain_queue(real_addon.crawl_queue)
    assert crawl_urls == ["http://test.com/search?q=fromlink"]

    scan_entries = _drain_queue(real_addon.scan_queue)
    # One parameterized-link target plus one form target.
    assert len(scan_entries) == 2
    # Link params come from urllib.parse.parse_qs (values are lists); form
    # params are plain string values.
    link_entry = next(
        entry for entry in scan_entries if entry["url"].endswith("?q=fromlink")
    )
    assert link_entry["method"] == "GET"
    assert link_entry["params"] == {"q": ["fromlink"]}

    form_entry = next(
        entry for entry in scan_entries if entry["url"] == "http://test.com/search"
    )
    assert form_entry["method"] == "GET"
    assert form_entry["params"] == {"q": "fromform"}

    # Separate signature namespaces must both be populated.
    assert any(s.startswith("link::") for s in real_addon.scanned_targets)
    assert any(s.startswith("form::") for s in real_addon.scanned_targets)


@pytest.mark.asyncio
async def test_response_hook_is_idempotent_for_duplicate_responses(real_addon):
    """
    Acceptance (via the plugin response hook): feeding the same response
    through the hook a second time must not enqueue any duplicate crawl or
    scan target.
    """
    html = """
    <html><body>
      <a href="/search?q=fromlink">Search link</a>
      <form action="/search" method="GET">
        <input name="q" value="fromform">
      </form>
      <form action="/switch" method="GET">
        <input type="hidden" name="mode" value="dark">
      </form>
    </body></html>
    """
    flow = _build_flow(html)
    real_addon.response(flow)
    await _flush_hook_tasks()

    first_crawl = _drain_queue(real_addon.crawl_queue)
    first_scans = _drain_queue(real_addon.scan_queue)
    assert len(first_crawl) == 1
    # Parameterized link + two forms.
    assert len(first_scans) == 3

    # Process the exact same response again.
    real_addon.response(flow)
    await _flush_hook_tasks()

    assert real_addon.crawl_queue.empty()
    assert real_addon.scan_queue.empty()


@pytest.mark.asyncio
async def test_response_hook_ignores_out_of_scope_responses(real_addon):
    """Scope filtering still applies at the plugin entry point."""
    flow = tflow.tflow(resp=True)
    flow.request = http.Request.make("GET", "http://evil.example/page")
    flow.response = http.Response.make(
        200,
        content=b'<a href="/anything">x</a>',
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    real_addon.response(flow)
    await _flush_hook_tasks()

    assert real_addon.crawl_queue.empty()
    assert real_addon.scan_queue.empty()
