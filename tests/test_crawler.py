# tests/test_crawler.py
import pytest
from unittest.mock import MagicMock
from urllib.parse import urlencode

try:
    from nightcrawler.crawler import discover_and_queue_targets
    from nightcrawler.utils import (
        create_target_signature,
        TARGET_TYPE_LINK,
        TARGET_TYPE_FORM,
    )
    from mitmproxy.http import Request  # Import Request for creating mock objects
except ImportError as e:
    pytest.fail(f"Could not import crawler functions or Request: {e}", pytrace=False)

# Fixtures mock_addon and others are loaded from conftest.py


@pytest.fixture
def smart_mock_addon(mocker, mock_addon):
    """Adds queues and sets needed by the crawler to the mock addon."""
    mock_addon.scan_queue = MagicMock()
    mock_addon.scan_queue.put_nowait = MagicMock(name="scan_queue_put")
    mock_addon.crawl_queue = MagicMock()
    mock_addon.crawl_queue.put_nowait = MagicMock(name="crawl_queue_put")
    mock_addon.scanned_targets = set()
    mock_addon.discovered_urls = set()
    mock_addon.logger = MagicMock(spec=["debug", "info", "warn", "error"])
    # The crawler needs the scope to be set on the addon instance
    mock_addon.effective_scope = {"test.com"}
    return mock_addon


def test_crawler_finds_in_scope_links(mocker, smart_mock_addon):
    """Test: The crawler should queue in-scope links and ignore out-of-scope ones."""
    # This test will now use the REAL is_in_scope function, as we've set
    # smart_mock_addon.effective_scope. No need to patch.
    html_content = """
    <a href="/about.html">About Us</a>
    <a href="https://test.com/contact">Contact</a>
    <a href="https://external.com/page">External Site</a>
    """
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    # Assert that the crawl queue was called exactly twice for the in-scope links
    assert smart_mock_addon.crawl_queue.put_nowait.call_count == 2
    smart_mock_addon.crawl_queue.put_nowait.assert_any_call(
        "http://test.com/about.html"
    )
    # --- CORRECTED ASSERTION: The test now expects the correct 'https' scheme ---
    smart_mock_addon.crawl_queue.put_nowait.assert_any_call("https://test.com/contact")


def test_crawler_finds_post_form_for_scanning(smart_mock_addon):
    """Test: The crawler should find a POST form and queue it for active scanning."""
    html_content = '<form action="/login" method="POST"><input name="user"><input name="pass"></form>'
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    smart_mock_addon.scan_queue.put_nowait.assert_called_once()
    call_args, _ = smart_mock_addon.scan_queue.put_nowait.call_args
    scan_details = call_args[0]

    assert scan_details["method"] == "POST"
    assert scan_details["url"] == "http://test.com/login"
    assert "user" in scan_details["data"]
    assert "pass" in scan_details["data"]
    smart_mock_addon.crawl_queue.put_nowait.assert_not_called()


def test_crawler_finds_get_form_for_scanning(smart_mock_addon):
    """Test: The crawler should find a GET form and queue it for active scanning."""
    html_content = '<form action="/search" method="GET"><input name="q"></form>'
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    smart_mock_addon.scan_queue.put_nowait.assert_called_once()
    call_args, _ = smart_mock_addon.scan_queue.put_nowait.call_args
    scan_details = call_args[0]

    assert scan_details["method"] == "GET"
    assert scan_details["url"] == "http://test.com/search"
    assert "q" in scan_details["params"]
    assert not scan_details["data"]


def test_crawler_avoids_duplicate_form_scans(smart_mock_addon):
    """Test: The crawler should not queue a form for scanning if it's already been seen."""
    html_content = '<form action="/login" method="POST"><input name="user"></form>'

    # Create a mock request matching the form the crawler will build:
    # fields without an explicit value default to 'nightcrawler_test', and the
    # signature must be created in the form namespace.
    mock_req = Request.make("POST", "http://test.com/login", content=b"user=nightcrawler_test")
    mock_req.urlencoded_form = {"user": "nightcrawler_test"}

    # --- CORRECTED CALL: Pass a mock logger to the signature function ---
    signature = create_target_signature(
        mock_req,
        logger=MagicMock(),
        target_type=TARGET_TYPE_FORM,
    )
    smart_mock_addon.scanned_targets.add(signature)

    # Run discovery
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    # Assert that nothing was queued for scanning
    smart_mock_addon.scan_queue.put_nowait.assert_not_called()


def test_forms_same_action_and_param_names_but_different_defaults_both_queued(
    smart_mock_addon,
):
    """
    Regression test: two forms sharing the same action and parameter names but
    carrying different pre-filled default values must each be enqueued for
    scanning instead of collapsing onto a single signature.
    """
    html_content = """
    <form action="/switch" method="GET">
        <input type="hidden" name="mode" value="light">
    </form>
    <form action="/switch" method="GET">
        <input type="hidden" name="mode" value="dark">
    </form>
    """
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    assert smart_mock_addon.scan_queue.put_nowait.call_count == 2
    queued_values = []
    for call in smart_mock_addon.scan_queue.put_nowait.call_args_list:
        scan_details = call[0][0]
        assert scan_details["method"] == "GET"
        assert scan_details["url"] == "http://test.com/switch"
        queued_values.append(scan_details["params"]["mode"])

    # Each form's distinct default value must survive into its queue entry.
    assert sorted(queued_values) == ["dark", "light"]

    # The two signatures stored for dedup must themselves be distinct.
    form_signatures = [
        sig for sig in smart_mock_addon.scanned_targets if sig.startswith("form::")
    ]
    assert len(form_signatures) == 2


def test_post_forms_different_defaults_both_queued(smart_mock_addon):
    """
    Regression test for POST bodies: same action/field names with different
    default values must produce distinct form signatures.
    """
    html_content = """
    <form action="/login" method="POST">
        <input type="hidden" name="user" value="alice">
    </form>
    <form action="/login" method="POST">
        <input type="hidden" name="user" value="bob">
    </form>
    """
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    assert smart_mock_addon.scan_queue.put_nowait.call_count == 2
    queued_users = sorted(
        call[0][0]["data"]["user"]
        for call in smart_mock_addon.scan_queue.put_nowait.call_args_list
    )
    assert queued_users == ["alice", "bob"]


def test_link_and_form_same_path_and_param_names_coexist(smart_mock_addon):
    """
    Regression test: a link carrying a query parameter and a form targeting the
    same path with the same parameter name must land in different queues
    (crawl_queue vs scan_queue) and must not swallow each other's signatures.
    """
    html_content = """
    <a href="/search?q=fromlink">Search link</a>
    <form action="/search" method="GET">
        <input name="q" value="fromform">
    </form>
    """
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    # The link is queued for crawling exactly once.
    smart_mock_addon.crawl_queue.put_nowait.assert_called_once_with(
        "http://test.com/search?q=fromlink"
    )

    # Both the parameterized link AND the form are queued for scanning.
    assert smart_mock_addon.scan_queue.put_nowait.call_count == 2
    link_scan = next(
        call[0][0]
        for call in smart_mock_addon.scan_queue.put_nowait.call_args_list
        if "?" in call[0][0]["url"]
    )
    # Link params come from urllib.parse.parse_qs (values are lists).
    assert link_scan["method"] == "GET"
    assert link_scan["params"] == {"q": ["fromlink"]}

    form_scan = next(
        call[0][0]
        for call in smart_mock_addon.scan_queue.put_nowait.call_args_list
        if "?" not in call[0][0]["url"]
    )
    assert form_scan["method"] == "GET"
    assert form_scan["params"] == {"q": "fromform"}

    # The link and the form occupy separate signature namespaces.
    assert any(
        sig.startswith("link::") for sig in smart_mock_addon.scanned_targets
    )
    assert any(
        sig.startswith("form::") for sig in smart_mock_addon.scanned_targets
    )


def test_links_deduplicated_by_parameter_names_only(smart_mock_addon):
    """
    Links sharing the same path and parameter names but different query values
    must still collapse onto a single scan target (value-independent link
    dedup), while both remain a single crawl entry per absolute URL.
    """
    html_content = """
    <a href="/search?q=alpha">Alpha</a>
    <a href="/search?q=beta">Beta</a>
    """
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    # Two distinct absolute URLs are both crawled...
    assert smart_mock_addon.crawl_queue.put_nowait.call_count == 2
    # ...but only one parameterized link scan target is enqueued.
    assert smart_mock_addon.scan_queue.put_nowait.call_count == 1
    link_signatures = [
        sig for sig in smart_mock_addon.scanned_targets if sig.startswith("link::")
    ]
    assert len(link_signatures) == 1
    assert link_signatures[0] == "link::GET::http://test.com/search::q"


def test_reprocessing_same_response_does_not_duplicate_enqueues(smart_mock_addon):
    """
    Processing the same response twice must not enqueue any link or form a
    second time: both discovered_urls and scanned_targets dedup.
    """
    html_content = """
    <a href="/search?q=fromlink">Search link</a>
    <form action="/search" method="GET">
        <input name="q" value="fromform">
    </form>
    <form action="/switch" method="GET">
        <input type="hidden" name="mode" value="dark">
    </form>
    """
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)
    first_crawl_calls = smart_mock_addon.crawl_queue.put_nowait.call_count
    first_scan_calls = smart_mock_addon.scan_queue.put_nowait.call_count
    assert first_crawl_calls == 1
    # Parameterized link + two distinct forms = three scan targets.
    assert first_scan_calls == 3

    # Reprocess the exact same response.
    discover_and_queue_targets(html_content, "http://test.com/", smart_mock_addon)

    assert smart_mock_addon.crawl_queue.put_nowait.call_count == first_crawl_calls
    assert smart_mock_addon.scan_queue.put_nowait.call_count == first_scan_calls
