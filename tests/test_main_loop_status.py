"""Offline test: twitter collector outcome reaches the web status layer."""

import asyncio
import sys
import types

import pytest

import collectors.twitter as twitter
import main as main_module


class FakeTweet:
    def __init__(self, tweet_id, text):
        self.id = tweet_id
        self.text = text
        self.user = types.SimpleNamespace(screen_name="alpha_trader")
        self.favorite_count = 1
        self.retweet_count = 0
        self.reply_count = 0


class _StopLoop(Exception):
    """Raised from the mocked sleep so run_collector_loop runs exactly once."""


class FakeAnalyzer:
    async def process_items(self, items, storage):
        return []


def _install_web_spy(monkeypatch):
    captured: list[dict] = []

    def fake_update(name, **kwargs):
        captured.append({"name": name, **kwargs})

    # main.py imports update_collector_status lazily inside _update_status.
    fake_web = types.ModuleType("web.app")
    fake_web.update_collector_status = fake_update
    monkeypatch.setitem(sys.modules, "web.app", fake_web)
    return captured


def _run_one_iteration(monkeypatch, collect_coro_factory):
    captured = _install_web_spy(monkeypatch)

    async def fake_collect():
        return await collect_coro_factory()

    async def fake_sleep(_seconds):
        raise _StopLoop

    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)

    async def main_coro():
        with pytest.raises(_StopLoop):
            await main_module.run_collector_loop(
                "twitter",
                fake_collect,
                interval_min=30,
                storage=object(),
                analyzer=FakeAnalyzer(),
            )

    asyncio.run(main_coro())
    return captured


def test_loop_propagates_twikit_success_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(twitter.settings, "twitter_cookies_file", str(tmp_path / "c.json"))
    monkeypatch.setattr(twitter.settings, "twitter_keywords", ["kw"])
    monkeypatch.setattr(twitter.settings, "twitter_username", "")
    monkeypatch.setattr(twitter.settings, "twitter_password", "")
    _write_cookies(tmp_path / "c.json")

    class FakeClient:
        def __init__(self, language=None):
            pass

        def set_cookies(self, cookies):
            pass

        async def search_tweet(self, keyword, product=None, count=None):
            return [FakeTweet("1", "healthy tweet")]

    fake_mod = types.ModuleType("twikit")
    fake_mod.Client = FakeClient
    fake_mod.errors = types.SimpleNamespace(TwitterException=Exception)
    monkeypatch.setitem(sys.modules, "twikit", fake_mod)

    captured = _run_one_iteration(monkeypatch, twitter.collect)

    idle = [c for c in captured if c["status"] == "idle"]
    assert idle, captured
    final = idle[-1]
    assert final["name"] == "twitter"
    assert final["method"] == "twikit"
    assert final["reason"] == twitter.REASON_TWIKIT_OK
    assert final["fallback"] is False
    assert final["warning"] == ""


def test_loop_propagates_fallback_outcome(tmp_path, monkeypatch):
    # Cookie file missing -> direct fallback; stub RSS so no network is used.
    monkeypatch.setattr(twitter.settings, "twitter_cookies_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(twitter.settings, "twitter_keywords", ["kw"])
    monkeypatch.setattr(twitter.settings, "twitter_username", "")
    monkeypatch.setattr(twitter.settings, "twitter_password", "")
    # Provide a fake twikit so the missing-cookie reason is the one recorded
    # (real twikit is not installed in the offline test environment).
    fake_mod = types.ModuleType("twikit")
    fake_mod.Client = type("FakeClient", (), {})
    fake_mod.errors = types.SimpleNamespace(TwitterException=Exception)
    monkeypatch.setitem(sys.modules, "twikit", fake_mod)

    async def fake_rss():
        from storage.models import RawItem, SourceType

        return [
            RawItem(
                source=SourceType.RSS,
                url="https://news.example/x",
                title="News",
                metadata={"method": "google_news_rss"},
            )
        ]

    monkeypatch.setattr(twitter, "_collect_google_news", fake_rss)

    captured = _run_one_iteration(monkeypatch, twitter.collect)

    idle = [c for c in captured if c["status"] == "idle"]
    assert idle, captured
    final = idle[-1]
    assert final["method"] == "google_news_rss"
    assert final["reason"] == twitter.REASON_COOKIES_MISSING
    assert final["fallback"] is True
    assert "Cookies missing" in final["warning"]


def _write_cookies(path, cookies=None):
    import json

    path.write_text(
        json.dumps(cookies or {"auth_token": "tok", "ct0": "ct"}), encoding="utf-8"
    )
