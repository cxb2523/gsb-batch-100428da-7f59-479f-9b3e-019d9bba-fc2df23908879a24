"""Offline tests for the Twitter/X collector outcome marking.

No network access and no real twikit are used: a fake twikit module is
injected into ``sys.modules`` and ``httpx.AsyncClient`` is replaced with an
in-memory fake that serves canned RSS XML. Every assertion therefore runs
fully offline.
"""

import asyncio
import sys
import types
from urllib.parse import parse_qs, urlparse

import pytest

from collectors import twitter
from collectors.twitter import (
    GOOGLE_NEWS_RSS,
    REASON_COOKIES_INVALID,
    REASON_COOKIES_MISSING,
    REASON_COOKIES_UNREADABLE,
    REASON_TWIKIT_NOT_INSTALLED,
    REASON_TWIKIT_OK,
    REASON_TWIKIT_REQUEST_FAILED,
)
from storage.models import RawItem, SourceType

KEYWORDS = ["polymarket strategy", "MEV bot"]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeUser:
    def __init__(self, screen_name):
        self.screen_name = screen_name


class FakeTweet:
    def __init__(self, tweet_id, text, screen_name="alpha_trader", fav=5, rt=2, reply=1):
        self.id = tweet_id
        self.text = text
        self.user = FakeUser(screen_name)
        self.favorite_count = fav
        self.retweet_count = rt
        self.reply_count = reply


class FakeRateLimitError(Exception):
    """Stands in for twikit.errors.TooManyRequests."""


def make_fake_twikit(handler, created):
    """Build a fake ``twikit`` module.

    ``handler(keyword, product)`` is an async callable returning a list of
    tweets or raising. ``created`` collects every instantiated client so
    tests can assert whether a session was built at all.
    """

    class FakeClient:
        def __init__(self, language=None):
            self.language = language
            self.cookies = None
            created.append(self)

        def set_cookies(self, cookies):
            self.cookies = dict(cookies)

        async def search_tweet(self, keyword, product=None, count=None):
            return await handler(keyword, product)

    mod = types.ModuleType("twikit")
    mod.Client = FakeClient
    mod.errors = types.SimpleNamespace(
        TwitterException=Exception,
        TooManyRequests=FakeRateLimitError,
    )
    return mod


class _BlockTwikitImport:
    """Meta-path finder that makes ``import twikit`` raise ImportError."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "twikit":
            raise ImportError("No module named 'twikit' (blocked by test)")
        return None


def _rss_item(url, title, source, summary="some summary", published="Mon, 01 Jan 2024 00:00:00 GMT"):
    return f"""<item>
  <title>{title}</title>
  <link>{url}</link>
  <guid isPermaLink="false">{url}</guid>
  <pubDate>{published}</pubDate>
  <description>{summary}</description>
  <source url="https://news.google.com/">{source}</source>
</item>"""


def _rss_feed(items_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>Google News</title>'
        + items_xml
        + "</channel></rss>"
    )


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def install_fake_httpx(monkeypatch, router):
    """Replace httpx.AsyncClient; record every GET URL.

    ``router(url) -> xml_text`` decides what each request receives.
    """
    calls: list[str] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")
            self.follow_redirects = kwargs.get("follow_redirects")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            calls.append(url)
            return FakeResponse(router(url))

    monkeypatch.setattr(twitter.httpx, "AsyncClient", FakeAsyncClient)
    return calls


def _write_cookies(path, cookies=None):
    import json

    path.write_text(json.dumps(cookies or {"auth_token": "tok", "ct0": "ct"}), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def configured(tmp_path, monkeypatch):
    """Standard settings: two keywords, cookie file under tmp_path, no login.

    A fake twikit module is injected by default (overridable in individual
    tests); use the ``no_twikit`` fixture to simulate twikit being absent.
    """
    cookies_file = tmp_path / "twitter_cookies.json"
    monkeypatch.setattr(twitter.settings, "twitter_cookies_file", str(cookies_file))
    monkeypatch.setattr(twitter.settings, "twitter_keywords", list(KEYWORDS))
    monkeypatch.setattr(twitter.settings, "twitter_username", "")
    monkeypatch.setattr(twitter.settings, "twitter_password", "")
    # Reset module-level outcome state before every test.
    monkeypatch.setattr(twitter, "last_method", "")
    monkeypatch.setattr(twitter, "last_reason", "")
    monkeypatch.setattr(twitter, "last_warning", "")
    monkeypatch.setattr(twitter, "last_fallback", False)

    async def default_handler(keyword, product):
        raise AssertionError(
            "twikit search unexpectedly reached without a test-defined handler"
        )

    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(default_handler, []))
    return cookies_file


@pytest.fixture
def no_twikit(monkeypatch):
    """Simulate twikit not being installed at all."""
    finder = _BlockTwikitImport()
    monkeypatch.setattr(sys, "meta_path", [finder] + sys.meta_path)
    monkeypatch.delitem(sys.modules, "twikit", raising=False)


def _rss_router(url_to_feed):
    def router(url):
        if url not in url_to_feed:
            raise AssertionError(f"unexpected request: {url}")
        return url_to_feed[url]

    return router


def _expected_rss_url(keyword):
    from urllib.parse import quote_plus

    return f"{GOOGLE_NEWS_RSS}?q={quote_plus(keyword)}&hl=en-US&gl=US&ceid=US:en"


# ---------------------------------------------------------------------------
# twikit success branch
# ---------------------------------------------------------------------------
def test_twikit_success_marks_outcome_and_clears_stale_warning(configured, monkeypatch):
    async def handler(keyword, product):
        return [FakeTweet(f"id-{keyword}", f"tweet about {keyword}")]

    created: list = []
    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(handler, created))
    # Any RSS call would fail the test: a healthy run must not fall back.
    calls = install_fake_httpx(monkeypatch, lambda url: pytest.fail("RSS must not be called"))
    # Simulate a stale warning from a previous, failed run.
    monkeypatch.setattr(twitter, "last_warning", "stale failure from previous run")
    _write_cookies(configured)

    items = asyncio.run(twitter.collect())

    assert len(created) == 1
    assert len(items) == len(KEYWORDS)
    assert all(isinstance(i, RawItem) for i in items)
    assert {i.source for i in items} == {SourceType.TWITTER}
    assert {i.metadata["method"] for i in items} == {"twikit"}
    assert items[0].url.startswith("https://x.com/alpha_trader/status/id-")
    assert twitter.last_method == "twikit"
    assert twitter.last_reason == REASON_TWIKIT_OK
    assert twitter.last_fallback is False
    assert twitter.last_warning == ""  # stale warning cleared
    assert calls == []


def test_twikit_empty_result_list_is_success_not_fallback(configured, monkeypatch):
    """A request that succeeds with zero tweets is not a failure."""
    async def handler(keyword, product):
        return []

    created: list = []
    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(handler, created))
    calls = install_fake_httpx(monkeypatch, lambda url: pytest.fail("RSS must not be called"))
    _write_cookies(configured)

    items = asyncio.run(twitter.collect())

    assert items == []
    assert twitter.last_method == "twikit"
    assert twitter.last_reason == REASON_TWIKIT_OK
    assert twitter.last_fallback is False
    assert twitter.last_warning == ""
    assert calls == []


def test_top_succeeds_after_latest_failure_counts_as_success(configured, monkeypatch):
    seen_products: list[tuple[str, str]] = []

    async def handler(keyword, product):
        seen_products.append((keyword, product))
        if product == "Latest":
            raise FakeRateLimitError("429 on Latest")
        return [FakeTweet(f"t-{keyword}-{product}", f"{keyword} top tweet")]
    created: list = []
    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(handler, created))
    calls = install_fake_httpx(monkeypatch, lambda url: pytest.fail("RSS must not be called"))
    _write_cookies(configured)

    items = asyncio.run(twitter.collect())

    # Latest failed then Top succeeded for every keyword -> keyword is OK.
    assert seen_products == [
        (KEYWORDS[0], "Latest"),
        (KEYWORDS[0], "Top"),
        (KEYWORDS[1], "Latest"),
        (KEYWORDS[1], "Top"),
    ]
    assert len(items) == len(KEYWORDS)
    assert {i.metadata["keyword"] for i in items} == set(KEYWORDS)
    assert twitter.last_method == "twikit"
    assert twitter.last_reason == REASON_TWIKIT_OK
    assert twitter.last_fallback is False
    assert twitter.last_warning == ""
    assert calls == []


def test_partial_keyword_failure_keeps_twikit_results(configured, monkeypatch):
    async def handler(keyword, product):
        if keyword == KEYWORDS[0]:
            raise FakeRateLimitError("429")
        return [FakeTweet("99", "only working keyword tweet")]

    created: list = []
    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(handler, created))
    calls = install_fake_httpx(monkeypatch, lambda url: pytest.fail("RSS must not be called"))
    _write_cookies(configured)

    items = asyncio.run(twitter.collect())

    assert len(items) == 1
    assert items[0].metadata["keyword"] == KEYWORDS[1]
    assert twitter.last_method == "twikit"
    assert twitter.last_fallback is False
    assert twitter.last_warning == ""
    assert calls == []


def test_twikit_dedupes_tweets_across_keywords(configured, monkeypatch):
    async def handler(keyword, product):
        # Same tweet id matches both keywords.
        return [FakeTweet("dup", "duplicated tweet"), FakeTweet("unique", "other")]

    created: list = []
    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(handler, created))
    _write_cookies(configured)

    items = asyncio.run(twitter.collect())

    ids = {i.url.rsplit("/", 1)[-1] for i in items}
    assert ids == {"dup", "unique"}
    assert twitter.last_method == "twikit"
    assert twitter.last_fallback is False


# ---------------------------------------------------------------------------
# Cookie-missing direct fallback branch
# ---------------------------------------------------------------------------
def test_missing_cookie_file_falls_back_directly(configured, monkeypatch):
    # Cookie file (the `configured` path) is deliberately never created.
    created: list = []
    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(
        lambda k, p: pytest.fail("twikit must not be called without cookies"), created))
    feeds = {
        _expected_rss_url(KEYWORDS[0]): _rss_feed(
            _rss_item("https://news.example/a", "News A", "Example News")
        ),
        _expected_rss_url(KEYWORDS[1]): _rss_feed(
            _rss_item("https://news.example/b", "News B", "Other News")
        ),
    }
    calls = install_fake_httpx(monkeypatch, _rss_router(feeds))

    items = asyncio.run(twitter.collect())

    assert created == []  # no twikit session built at all
    assert len(items) == 2
    assert twitter.last_method == "google_news_rss"
    assert twitter.last_reason == REASON_COOKIES_MISSING
    assert twitter.last_fallback is True
    assert "Cookies missing" in twitter.last_warning
    # Exactly one RSS request per keyword, in order.
    assert calls == [_expected_rss_url(k) for k in KEYWORDS]


def test_cookies_missing_auth_token_marked_invalid(configured, monkeypatch):
    _write_cookies(configured, cookies={"session": "no-auth-cookies"})
    feeds = {
        _expected_rss_url(k): _rss_feed(
            _rss_item(f"https://news.example/{i}", f"News {i}", "Src")
        )
        for i, k in enumerate(KEYWORDS)
    }
    calls = install_fake_httpx(monkeypatch, _rss_router(feeds))

    items = asyncio.run(twitter.collect())

    assert len(items) == 2
    assert twitter.last_method == "google_news_rss"
    assert twitter.last_reason == REASON_COOKIES_INVALID
    assert twitter.last_fallback is True
    assert "invalid" in twitter.last_warning.lower()
    assert len(calls) == len(KEYWORDS)


def test_corrupt_cookie_file_marked_unreadable(configured, monkeypatch):
    configured.write_text("{not valid json", encoding="utf-8")
    feeds = {
        _expected_rss_url(k): _rss_feed(
            _rss_item(f"https://news.example/{i}", f"News {i}", "Src")
        )
        for i, k in enumerate(KEYWORDS)
    }
    install_fake_httpx(monkeypatch, _rss_router(feeds))

    items = asyncio.run(twitter.collect())

    assert twitter.last_method == "google_news_rss"
    assert twitter.last_reason == REASON_COOKIES_UNREADABLE
    assert twitter.last_fallback is True
    assert twitter.last_warning
    assert len(items) == 2


def test_twikit_not_installed_reports_dedicated_reason(configured, no_twikit, monkeypatch):
    _write_cookies(configured)  # cookies exist, but the library is absent
    feeds = {
        _expected_rss_url(k): _rss_feed(
            _rss_item(f"https://news.example/{i}", f"News {i}", "Src")
        )
        for i, k in enumerate(KEYWORDS)
    }
    install_fake_httpx(monkeypatch, _rss_router(feeds))

    items = asyncio.run(twitter.collect())

    assert twitter.last_method == "google_news_rss"
    assert twitter.last_reason == REASON_TWIKIT_NOT_INSTALLED
    assert twitter.last_fallback is True
    assert "twikit not installed" in twitter.last_warning
    assert len(items) == 2


# ---------------------------------------------------------------------------
# Cookies present but twikit requests failing -> fallback branch
# ---------------------------------------------------------------------------
def test_all_twikit_requests_failing_falls_back_with_request_reason(
    configured, monkeypatch
):
    async def handler(keyword, product):
        raise FakeRateLimitError("429 Too Many Requests")

    created: list = []
    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(handler, created))
    feeds = {
        _expected_rss_url(k): _rss_feed(
            _rss_item(f"https://news.example/{i}", f"News {i}", "Example News")
        )
        for i, k in enumerate(KEYWORDS)
    }
    calls = install_fake_httpx(monkeypatch, _rss_router(feeds))
    _write_cookies(configured)

    items = asyncio.run(twitter.collect())

    # Session was built (cookies existed) but every request failed.
    assert len(created) == 1
    assert twitter.last_method == "google_news_rss"
    assert twitter.last_reason == REASON_TWIKIT_REQUEST_FAILED
    assert twitter.last_fallback is True
    assert "rate limit" in twitter.last_warning.lower()
    assert items and all(i.source == SourceType.RSS for i in items)
    assert calls  # RSS fallback actually ran


def test_session_level_exception_also_marks_request_failure(configured, monkeypatch):
    class ExplodingClient:
        def __init__(self, language=None):
            pass

        def set_cookies(self, cookies):
            pass

        async def search_tweet(self, keyword, product=None, count=None):
            raise RuntimeError("session expired")

    fake_mod = types.ModuleType("twikit")
    fake_mod.Client = ExplodingClient
    fake_mod.errors = types.SimpleNamespace(TwitterException=Exception)
    monkeypatch.setitem(sys.modules, "twikit", fake_mod)
    feeds = {
        _expected_rss_url(k): _rss_feed(
            _rss_item(f"https://news.example/{i}", f"News {i}", "Src")
        )
        for i, k in enumerate(KEYWORDS)
    }
    install_fake_httpx(monkeypatch, _rss_router(feeds))
    _write_cookies(configured)

    # Per-keyword try/except handles search errors; force the outer guard by
    # making *every* product fail, which collapses to the same reason.
    items = asyncio.run(twitter.collect())

    assert twitter.last_reason == REASON_TWIKIT_REQUEST_FAILED
    assert twitter.last_fallback is True
    assert twitter.last_method == "google_news_rss"
    assert len(items) == 2


# ---------------------------------------------------------------------------
# Fallback keeps Google News RSS params, mapping and dedup unchanged
# ---------------------------------------------------------------------------
def test_google_news_fallback_preserves_params_mapping_and_dedup(
    configured, monkeypatch
):
    # No cookies -> direct fallback path exercises the RSS collector.
    shared_url = "https://news.example/shared"
    feeds = {
        _expected_rss_url(KEYWORDS[0]): _rss_feed(
            _rss_item(shared_url, "Shared Title", "Shared Source", summary="shared summary")
            + _rss_item(
                "https://news.example/unique-1",
                "Unique One",
                "Source One",
                summary="first summary",
                published="Tue, 02 Jan 2024 00:00:00 GMT",
            )
        ),
        _expected_rss_url(KEYWORDS[1]): _rss_feed(
            _rss_item(shared_url, "Shared Title", "Shared Source", summary="shared summary")
            + _rss_item("https://news.example/unique-2", "Unique Two", "Source Two")
        ),
    }
    calls = install_fake_httpx(monkeypatch, _rss_router(feeds))

    items = asyncio.run(twitter.collect())

    # 3 unique URLs: the shared one appears in both feeds but is deduped.
    assert len(items) == 3
    by_url = {i.url: i for i in items}
    shared = by_url[shared_url]
    assert shared.source == SourceType.RSS
    assert shared.title == "Shared Title"
    assert shared.content == "shared summary"
    assert shared.author == "Shared Source"
    assert shared.metadata == {
        "keyword": KEYWORDS[0],
        "published": "Mon, 01 Jan 2024 00:00:00 GMT",
        "method": "google_news_rss",
    }

    # Request params must remain exactly hl=en-US&gl=US&ceid=US:en&q=<keyword>.
    assert len(calls) == 2
    for url, keyword in zip(calls, KEYWORDS):
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "news.google.com"
        assert parsed.path == "/rss/search"
        qs = parse_qs(parsed.query)
        assert qs == {
            "q": [keyword],
            "hl": ["en-US"],
            "gl": ["US"],
            "ceid": ["US:en"],
        }

    assert twitter.last_fallback is True
    assert twitter.last_method == "google_news_rss"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def test_no_keywords_configured_skips_collection(configured, monkeypatch):
    monkeypatch.setattr(twitter.settings, "twitter_keywords", [])
    calls = install_fake_httpx(monkeypatch, lambda url: pytest.fail("no requests expected"))

    assert asyncio.run(twitter.collect()) == []
    assert calls == []


def test_recovery_after_failure_clears_warning(configured, monkeypatch):
    """Run N (failure) then run N+1 (success): no stale warning remains."""
    feeds = {
        _expected_rss_url(k): _rss_feed(
            _rss_item(f"https://news.example/{i}", f"News {i}", "Src")
        )
        for i, k in enumerate(KEYWORDS)
    }

    # Run 1: cookies present but twikit rate-limited.
    async def failing_handler(keyword, product):
        raise FakeRateLimitError("429")

    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(failing_handler, []))
    install_fake_httpx(monkeypatch, _rss_router(feeds))
    _write_cookies(configured)
    asyncio.run(twitter.collect())
    assert twitter.last_fallback is True
    assert twitter.last_warning
    assert twitter.last_reason == REASON_TWIKIT_REQUEST_FAILED

    # Run 2: session recovers.
    async def ok_handler(keyword, product):
        return [FakeTweet(f"ok-{keyword}", "healthy tweet")]
    monkeypatch.setitem(sys.modules, "twikit", make_fake_twikit(ok_handler, []))
    items = asyncio.run(twitter.collect())

    assert len(items) == len(KEYWORDS)
    assert twitter.last_method == "twikit"
    assert twitter.last_reason == REASON_TWIKIT_OK
    assert twitter.last_fallback is False
    assert twitter.last_warning == ""
