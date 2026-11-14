"""Per-geo settlement for the sync and async RSS batch functions (partial=True).

Offline only: the sync path replaces ``requests.get``; the async path replaces
``aiohttp.ClientSession`` with a fake that serves one region, times out on
another and never sees the invalid one (validation fails before the network).

Each test builds the same three-way batch — one success, one network failure,
one invalid geo — and checks that:

- ``partial=True`` yields one ``{"ok": ...}`` settlement per geo, with failures
  recorded under their own key instead of aborting the batch;
- the default (``partial`` omitted) still raises the original exception type.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from trendspyg import clear_rss_cache
from trendspyg.exceptions import DownloadError, InvalidParameterError

SAMPLE_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:ht="https://trends.google.com/trending/rss">
  <channel>
    <title>Trending Now</title>
    <item>
      <title>bitcoin</title>
      <ht:approx_traffic>500K+</ht:approx_traffic>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>ethereum</title>
      <ht:approx_traffic>100K+</ht:approx_traffic>
      <pubDate>Mon, 01 Jan 2024 11:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

GEOS = ["US", "GB", "INVALID"]


def _sync_get(url, **kwargs):
    """US serves the feed, GB times out; INVALID must never reach the network."""
    if "geo=GB" in url:
        import requests

        raise requests.Timeout("Request timed out")
    response = MagicMock()
    response.content = SAMPLE_RSS_XML
    response.raise_for_status = MagicMock()
    return response


class TestSyncBatchPartial:
    """download_google_trends_rss_batch: per-geo settlement and fail-fast default"""

    def setup_method(self):
        clear_rss_cache()

    @patch("trendspyg.rss_downloader.requests.get", side_effect=_sync_get)
    def test_partial_settles_each_geo(self, mock_get):
        """partial=True: success and both failure kinds are all reported."""
        from trendspyg.rss_downloader import download_google_trends_rss_batch

        results = download_google_trends_rss_batch(
            GEOS, show_progress=False, cache=False, partial=True
        )

        assert set(results) == set(GEOS)

        # Success carries the real data, unaffected by the failing siblings.
        assert results["US"]["ok"] is True
        assert [t["trend"] for t in results["US"]["data"]] == ["bitcoin", "ethereum"]

        # Timeout -> DownloadError recorded, not raised.
        assert results["GB"]["ok"] is False
        assert results["GB"]["error"] == "DownloadError"
        assert "timed out" in results["GB"]["message"]

        # Invalid geo is a parameter error, settled like any other failure.
        assert results["INVALID"]["ok"] is False
        assert results["INVALID"]["error"] == "InvalidParameterError"
        assert "Invalid geo code" in results["INVALID"]["message"]

    @patch("trendspyg.rss_downloader.requests.get", side_effect=_sync_get)
    def test_partial_normalize_keeps_envelope_in_success_branch(self, mock_get):
        """normalize=True: successes hold a NormalizedEnvelope under 'data'."""
        from trendspyg.rss_downloader import download_google_trends_rss_batch

        results = download_google_trends_rss_batch(
            GEOS, show_progress=False, cache=False, normalize=True, partial=True
        )

        assert results["US"]["ok"] is True
        envelope = results["US"]["data"]
        assert envelope["source"] == "rss"
        assert envelope["geo"] == "US"
        assert envelope["trends"][0]["keyword"] == "bitcoin"
        assert results["GB"]["ok"] is False
        assert results["INVALID"]["ok"] is False

    @patch("trendspyg.rss_downloader.requests.get", side_effect=_sync_get)
    def test_partial_delay_still_applies_between_geos(self, mock_get, monkeypatch):
        """A failed geo does not skip the inter-request delay."""
        from trendspyg.rss_downloader import download_google_trends_rss_batch

        sleeps = []
        monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

        download_google_trends_rss_batch(
            GEOS, show_progress=False, cache=False, delay=0.5, partial=True
        )

        assert sleeps == [0.5, 0.5, 0.5]

    @patch("trendspyg.rss_downloader.requests.get", side_effect=_sync_get)
    def test_default_raises_network_error(self, mock_get):
        """partial omitted: the first failing geo raises its original exception."""
        from trendspyg.rss_downloader import download_google_trends_rss_batch

        with pytest.raises(DownloadError) as exc_info:
            download_google_trends_rss_batch(["GB", "US"], show_progress=False, cache=False)

        assert "timed out" in str(exc_info.value)

    @patch("trendspyg.rss_downloader.requests.get", side_effect=_sync_get)
    def test_default_raises_invalid_geo(self, mock_get):
        """partial omitted: an invalid geo aborts the batch as before."""
        from trendspyg.rss_downloader import download_google_trends_rss_batch

        with pytest.raises(InvalidParameterError) as exc_info:
            download_google_trends_rss_batch(["US", "INVALID"], show_progress=False, cache=False)

        assert "Invalid geo code" in str(exc_info.value)

    @patch("trendspyg.rss_downloader.requests.get", side_effect=_sync_get)
    def test_default_shape_is_unwrapped(self, mock_get):
        """partial omitted: values stay raw trend lists, not {'ok': ...} dicts."""
        from trendspyg.rss_downloader import download_google_trends_rss_batch

        results = download_google_trends_rss_batch(["US"], show_progress=False, cache=False)

        assert isinstance(results["US"], list)
        assert results["US"][0]["trend"] == "bitcoin"


# --- Offline fake aiohttp layer for the real async engine -------------------


class _ConcurrencyTracker:
    """Counts in-flight reads so the semaphore cap can be asserted."""

    def __init__(self):
        self.active = 0
        self.max_active = 0

    def enter(self):
        self.active += 1
        self.max_active = max(self.max_active, self.active)

    def exit(self):
        self.active -= 1


class _FakeAsyncResponse:
    """aiohttp response stand-in; holds the 'request' open briefly."""

    def __init__(self, tracker, delay=0.01):
        self._tracker = tracker
        self._delay = delay
        self.status = 200

    async def __aenter__(self):
        self._tracker.enter()
        await asyncio.sleep(self._delay)
        return self

    async def __aexit__(self, *exc_info):
        self._tracker.exit()
        return False

    async def read(self):
        return SAMPLE_RSS_XML


class _FakeAsyncSession:
    """aiohttp.ClientSession stand-in: serves every geo except GB, which times out."""

    def __init__(self):
        self.tracker = _ConcurrencyTracker()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def get(self, url, **kwargs):
        if "geo=GB" in url:
            raise asyncio.TimeoutError()
        return _FakeAsyncResponse(self.tracker)

    async def close(self):
        pass


@pytest.mark.asyncio
class TestAsyncBatchPartial:
    """download_google_trends_rss_batch_async: settlement, isolation, concurrency"""

    def setup_method(self):
        clear_rss_cache()

    async def test_partial_settles_each_geo_through_real_engine(self, monkeypatch):
        """partial=True: fake transport serves US, times out on GB, INVALID is recorded."""
        import aiohttp

        from trendspyg.rss_downloader import download_google_trends_rss_batch_async

        session = _FakeAsyncSession()
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: session)

        results = await download_google_trends_rss_batch_async(
            GEOS, show_progress=False, cache=False, partial=True
        )

        assert set(results) == set(GEOS)
        assert results["US"]["ok"] is True
        assert [t["trend"] for t in results["US"]["data"]] == ["bitcoin", "ethereum"]
        assert results["GB"]["ok"] is False
        assert results["GB"]["error"] == "DownloadError"
        assert "timed out" in results["GB"]["message"]
        assert results["INVALID"]["ok"] is False
        assert results["INVALID"]["error"] == "InvalidParameterError"
        assert "Invalid geo code" in results["INVALID"]["message"]

    async def test_partial_isolation_and_concurrency_cap(self, monkeypatch):
        """One geo failing never cancels others, and max_concurrent is respected."""
        import aiohttp

        from trendspyg.rss_downloader import download_google_trends_rss_batch_async

        session = _FakeAsyncSession()
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: session)

        geos = ["US", "GB", "CA", "DE", "FR", "JP"]
        results = await download_google_trends_rss_batch_async(
            geos, show_progress=False, cache=False, max_concurrent=2, partial=True
        )

        assert set(results) == set(geos)
        # 6 geos over a semaphore of 2: the cap must be reached but never passed.
        assert session.tracker.max_active == 2
        assert [geo for geo, r in results.items() if not r["ok"]] == ["GB"]
        # Every other geo kept its data despite GB failing mid-batch.
        for geo in geos:
            if geo != "GB":
                assert results[geo]["ok"] is True
                assert results[geo]["data"][0]["trend"] == "bitcoin"

    async def test_partial_normalize_keeps_envelope_in_success_branch(self, monkeypatch):
        """normalize=True: 'data' is the NormalizedEnvelope, failures stay errors."""
        import aiohttp

        from trendspyg.rss_downloader import download_google_trends_rss_batch_async

        session = _FakeAsyncSession()
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: session)

        results = await download_google_trends_rss_batch_async(
            GEOS, show_progress=False, cache=False, normalize=True, partial=True
        )

        assert results["US"]["data"]["source"] == "rss"
        assert results["US"]["data"]["geo"] == "US"
        assert results["US"]["data"]["trends"][0]["keyword"] == "bitcoin"
        assert results["GB"]["ok"] is False
        assert results["INVALID"]["ok"] is False

    async def test_partial_with_progress_bar(self, monkeypatch):
        """The tqdm path also settles failures instead of aborting."""
        import aiohttp

        from trendspyg.rss_downloader import download_google_trends_rss_batch_async

        session = _FakeAsyncSession()
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: session)

        results = await download_google_trends_rss_batch_async(
            GEOS, show_progress=True, cache=False, partial=True
        )

        assert results["US"]["ok"] is True
        assert results["GB"]["ok"] is False
        assert results["INVALID"]["ok"] is False

    async def test_default_raises_network_error(self, monkeypatch):
        """partial omitted: the failing geo surfaces its original exception."""
        import aiohttp

        from trendspyg.rss_downloader import download_google_trends_rss_batch_async

        session = _FakeAsyncSession()
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: session)

        with pytest.raises(DownloadError) as exc_info:
            await download_google_trends_rss_batch_async(
                ["GB", "US"], show_progress=False, cache=False
            )

        assert "timed out" in str(exc_info.value)

    async def test_default_raises_invalid_geo(self, monkeypatch):
        """partial omitted: an invalid geo still aborts the whole batch."""
        import aiohttp

        from trendspyg.rss_downloader import download_google_trends_rss_batch_async

        session = _FakeAsyncSession()
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: session)

        with pytest.raises(InvalidParameterError):
            await download_google_trends_rss_batch_async(
                ["US", "INVALID"], show_progress=False, cache=False
            )
