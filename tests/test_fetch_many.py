"""Local, offline tests for HomeBrowser.fetch_many.

``HomeBrowser.fetch`` is replaced with a stub and the clock is fully
controllable, so no browser, MCP server or network access is involved.
"""
import threading

import pytest

import hbm
from hbm import HomeBrowser


class FakeClock:
    """A thread-safe monotonic clock (seconds) that only moves when told to.

    Mirrors ``time.monotonic`` / ``time.sleep`` semantics so it can be
    injected directly into ``fetch_many(clock=..., sleep=...)``.
    """

    def __init__(self, start: float = 10.0):
        self._now = start
        self._lock = threading.Lock()

    def time(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, f"sleep called with negative duration: {seconds}"
        with self._lock:
            self._now += seconds

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds


class FakeFetch:
    """Records fetch calls and simulates a fetch that takes ``duration_ms``."""

    def __init__(self, clock: FakeClock, duration_ms: float = 500.0, fail_urls=()):
        self.clock = clock
        self.duration_ms = duration_ms
        self.fail_urls = set(fail_urls)
        self.calls = []  # list of dicts: url, extraction_type, started_ms
        self._lock = threading.Lock()

    def __call__(self, url, extraction_type="markdown", solve_cloudflare=True,
                 wait=None, network_idle=True):
        started_ms = self.clock.time() * 1000.0
        with self._lock:
            self.calls.append(
                {"url": url, "extraction_type": extraction_type, "started_ms": started_ms}
            )
        self.clock.advance(self.duration_ms / 1000.0)
        if url in self.fail_urls:
            raise RuntimeError(f"boom for {url}")
        return {"content": [f"payload for {url}"], "extraction_type": extraction_type}

    @property
    def started_times(self):
        with self._lock:
            return [c["started_ms"] for c in self.calls]


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def browser():
    return HomeBrowser(host=None, engine="scrapling")


def install_fake_fetch(monkeypatch, fake):
    monkeypatch.setattr(HomeBrowser, "fetch", fake)


def test_empty_list_returns_empty_without_fetching(monkeypatch, browser, clock):
    fake = FakeFetch(clock)
    install_fake_fetch(monkeypatch, fake)

    assert browser.fetch_many([], min_interval_ms=1000, clock=clock.time, sleep=clock.sleep) == []
    assert fake.calls == []


@pytest.mark.parametrize("bad_input", [None, "https://example.com", {"url": "x"}, 42])
def test_non_list_input_rejected_before_fetching(monkeypatch, browser, clock, bad_input):
    fake = FakeFetch(clock)
    install_fake_fetch(monkeypatch, fake)

    with pytest.raises(ValueError):
        browser.fetch_many(bad_input, min_interval_ms=1000, clock=clock.time, sleep=clock.sleep)
    assert fake.calls == []


@pytest.mark.parametrize(
    "bad_request",
    [{}, {"extraction_type": "markdown"}, {"url": ""}, {"url": "   "}, {"url": None}, {"url": 123}],
)
def test_invalid_item_rejected_before_any_fetch(monkeypatch, browser, clock, bad_request):
    fake = FakeFetch(clock)
    install_fake_fetch(monkeypatch, fake)

    # A valid request comes first; validation must still happen before any fetch.
    with pytest.raises(ValueError):
        browser.fetch_many(
            [{"url": "https://good.example/a"}, bad_request],
            min_interval_ms=1000,
            clock=clock.time,
            sleep=clock.sleep,
        )
    assert fake.calls == []


def test_same_hostname_consecutive_items_accumulate_wait(monkeypatch, browser, clock):
    fake = FakeFetch(clock, duration_ms=500)
    install_fake_fetch(monkeypatch, fake)

    requests = [
        {"url": "Example.COM/a"},
        {"url": "https://example.com/b"},  # same host, different case/scheme
        {"url": "example.com/c"},
    ]
    results = browser.fetch_many(
        requests, min_interval_ms=2000, clock=clock.time, sleep=clock.sleep
    )

    assert [r["url"] for r in results] == [r["url"] for r in requests]
    assert [r["ok"] for r in results] == [True, True, True]
    # Fetch duration (500ms) counts toward the interval, so each of the
    # second and third waits is 2000 - 500 = 1500 ms.
    assert results[0]["waited_ms"] == pytest.approx(0)
    assert results[1]["waited_ms"] == pytest.approx(1500)
    assert results[2]["waited_ms"] == pytest.approx(1500)

    starts = fake.started_times
    assert len(starts) == 3
    for earlier, later in zip(starts, starts[1:]):
        assert later - earlier == pytest.approx(2000)


def test_different_hostnames_do_not_wait(monkeypatch, browser, clock):
    fake = FakeFetch(clock, duration_ms=500)
    install_fake_fetch(monkeypatch, fake)

    requests = [
        {"url": "https://alpha.example/a"},
        {"url": "https://beta.example/b"},
        {"url": "https://gamma.example/c"},
    ]
    results = browser.fetch_many(
        requests, min_interval_ms=10_000, clock=clock.time, sleep=clock.sleep
    )

    assert [r["ok"] for r in results] == [True, True, True]
    assert [r["waited_ms"] for r in results] == [pytest.approx(0)] * 3
    assert sorted(c["url"] for c in fake.calls) == sorted(r["url"] for r in requests)


def test_interleaved_hosts_only_rate_limit_each_host(monkeypatch, browser, clock):
    fake = FakeFetch(clock, duration_ms=0)
    install_fake_fetch(monkeypatch, fake)

    requests = [
        {"url": "https://a.example/1"},
        {"url": "https://b.example/1"},
        {"url": "https://a.example/2"},
    ]
    results = browser.fetch_many(
        requests, min_interval_ms=2000, clock=clock.time, sleep=clock.sleep
    )

    assert results[0]["waited_ms"] == pytest.approx(0)
    assert results[1]["waited_ms"] == pytest.approx(0)
    assert results[2]["waited_ms"] == pytest.approx(2000)

    a_starts = [c["started_ms"] for c in fake.calls if c["url"].startswith("https://a.example")]
    assert a_starts[1] - a_starts[0] == pytest.approx(2000)


def test_failure_in_middle_does_not_stop_later_items(monkeypatch, browser, clock):
    fake = FakeFetch(clock, duration_ms=100, fail_urls={"https://same.example/2"})
    install_fake_fetch(monkeypatch, fake)

    requests = [
        {"url": "https://same.example/1"},
        {"url": "https://same.example/2"},
        {"url": "https://same.example/3"},
    ]
    results = browser.fetch_many(
        requests, min_interval_ms=2000, clock=clock.time, sleep=clock.sleep
    )

    assert len(results) == 3
    assert [r["url"] for r in results] == [r["url"] for r in requests]
    assert [r["ok"] for r in results] == [True, False, True]
    assert results[0]["output"]["content"] == ["payload for https://same.example/1"]
    assert isinstance(results[1]["output"], str)
    assert "boom for https://same.example/2" in results[1]["output"]
    assert results[2]["output"]["content"] == ["payload for https://same.example/3"]

    fetched_urls = [c["url"] for c in fake.calls]
    assert "https://same.example/3" in fetched_urls
    # The failed item still occupies a slot: the third item is rate-limited
    # relative to the failed attempt's start, not the first item's.
    assert all(isinstance(r["waited_ms"], float) for r in results)


def test_extraction_type_defaults_and_overrides(monkeypatch, browser, clock):
    fake = FakeFetch(clock)
    install_fake_fetch(monkeypatch, fake)

    results = browser.fetch_many(
        [
            {"url": "https://x.example/default"},
            {"url": "https://x.example/html", "extraction_type": "html"},
        ],
        min_interval_ms=0,
        clock=clock.time,
        sleep=clock.sleep,
    )

    assert len(results) == 2
    by_url = {c["url"]: c["extraction_type"] for c in fake.calls}
    assert by_url["https://x.example/default"] == "markdown"
    assert by_url["https://x.example/html"] == "html"


def test_results_have_required_fields(monkeypatch, browser, clock):
    fake = FakeFetch(clock, fail_urls={"https://r.example/bad"})
    install_fake_fetch(monkeypatch, fake)

    results = browser.fetch_many(
        [{"url": "https://r.example/ok"}, {"url": "https://r.example/bad"}],
        min_interval_ms=0,
        clock=clock.time,
        sleep=clock.sleep,
    )

    assert len(results) == 2
    for result in results:
        assert {"url", "ok", "output", "waited_ms"} <= result.keys()
        assert isinstance(result["ok"], bool)
        assert isinstance(result["waited_ms"], float)
        assert result["waited_ms"] >= 0
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert "boom" in results[1]["output"]


def test_raw_url_is_passed_through_to_fetch(monkeypatch, browser, clock):
    """Scheme completion stays inside fetch; the batch must not rewrite URLs."""
    fake = FakeFetch(clock)
    install_fake_fetch(monkeypatch, fake)

    results = browser.fetch_many(
        [{"url": "  example.com/no-scheme  "}],
        min_interval_ms=0,
        clock=clock.time,
        sleep=clock.sleep,
    )

    assert results[0]["url"] == "  example.com/no-scheme  "
    assert [c["url"] for c in fake.calls] == ["example.com/no-scheme"]


def test_default_interval_applies_when_not_specified(monkeypatch, browser, clock):
    fake = FakeFetch(clock, duration_ms=500)
    install_fake_fetch(monkeypatch, fake)

    results = browser.fetch_many(
        [{"url": "https://d.example/1"}, {"url": "https://d.example/2"}],
        clock=clock.time,
        sleep=clock.sleep,
    )

    expected_wait = hbm.DEFAULT_BATCH_MIN_INTERVAL_MS - 500
    assert results[1]["waited_ms"] == pytest.approx(expected_wait)
    starts = fake.started_times
    assert starts[1] - starts[0] == pytest.approx(hbm.DEFAULT_BATCH_MIN_INTERVAL_MS)


@pytest.mark.parametrize("bad_interval", [-1, float("nan"), float("inf"), "2000", True])
def test_invalid_min_interval_rejected(monkeypatch, browser, clock, bad_interval):
    fake = FakeFetch(clock)
    install_fake_fetch(monkeypatch, fake)

    with pytest.raises(ValueError):
        browser.fetch_many(
            [{"url": "https://i.example/1"}],
            min_interval_ms=bad_interval,
            clock=clock.time,
            sleep=clock.sleep,
        )
    assert fake.calls == []


def test_different_hostnames_run_concurrently(monkeypatch, browser, clock):
    """Three distinct hosts must be in flight at the same time."""
    hosts = ["https://one.example/x", "https://two.example/x", "https://three.example/x"]
    all_in_flight = threading.Event()
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0, "saw_parallel": False}

    class ConcurrentFetch:
        def __call__(self, url, extraction_type="markdown", **kwargs):
            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                if state["active"] == len(hosts):
                    all_in_flight.set()
            # Wait for the other hosts' fetches to arrive. Serial execution
            # would time out here rather than set the event.
            state["saw_parallel"] = all_in_flight.wait(timeout=5)
            with lock:
                state["active"] -= 1
            return {"content": [url]}

    monkeypatch.setattr(HomeBrowser, "fetch", ConcurrentFetch())
    results = browser.fetch_many(
        [{"url": host} for host in hosts],
        min_interval_ms=10_000,
        clock=clock.time,
        sleep=clock.sleep,
    )

    assert [r["ok"] for r in results] == [True, True, True]
    assert state["saw_parallel"] is True
    assert state["max_active"] == len(hosts)


