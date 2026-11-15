"""Offline tests for the aggregate `GET /v2/dramas/{slug}/full` endpoint.

The MDL client is fully faked, so none of these touch the network: each test
overrides `app.client.get`, the same rate-limited entry point the real scraper
uses (semaphore cap + queue timeout stay in place).
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import primp
import pytest
from fastapi.testclient import TestClient

from app import client as app_client
from app import main
from app.cache import TTLCache
from app.main import app

client = TestClient(app)

DETAIL_URL = "https://mydramalist.com/18452-goblin"
BASE = "18452-goblin"
OK_SECTION = {"status": "ok", "http": 200, "message": "OK"}


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "cache", TTLCache(ttl=60))


def fake_response(status: int, text: str = "<html></html>", url: str = DETAIL_URL):
    """Stands in for a primp response, including the `aread()` the client awaits."""

    async def aread() -> bytes:
        return text.encode()

    return SimpleNamespace(status_code=status, text=text, url=url, aread=aread)


def install_fake_get(
    monkeypatch: pytest.MonkeyPatch,
    behavior: dict[str, Any] | None = None,
    *,
    default_status: int = 200,
) -> list[tuple[str, Any]]:
    """Override the rate-limited `client.get`.

    `behavior` maps a path suffix ("" = detail, "/cast", "/episodes",
    "/reviews") to either an int status code or an Exception instance that the
    fake raises. Everything not listed answers `default_status`.
    Records every (path, params) call so tests can assert the fan-out.
    """
    behavior = behavior or {}
    calls: list[tuple[str, Any]] = []

    async def fake_get(path: str, params: Any = None) -> SimpleNamespace:
        calls.append((path, params))

        suffix = path.removeprefix(BASE)
        outcome = behavior.get(suffix, default_status)
        if isinstance(outcome, Exception):
            raise outcome
        return fake_response(outcome, url=DETAIL_URL + suffix)

    monkeypatch.setattr(app_client, "get", fake_get)
    return calls


def test_full_all_sections_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_fake_get(monkeypatch)

    r = client.get("/v2/dramas/18452-goblin/full")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"success", "data", "sections", "link", "scraped_at"}
    assert body["success"] is True
    assert body["link"] == DETAIL_URL
    assert body["scraped_at"]  # ISO timestamp from the detail scrape

    assert set(body["data"]) == {"details", "cast", "episodes", "reviews"}
    # the real parsers ran on the empty pages and produced their usual shape
    assert body["data"]["details"]["title"] == ""
    assert body["data"]["cast"] == {"title": "", "poster": "", "cast": {}}
    assert body["data"]["episodes"] == {
        "title": "",
        "poster": "",
        "episodes": [],
    }
    assert body["data"]["reviews"]["pagination"] == {"page": 1, "total_pages": 1}

    assert body["sections"] == {
        "details": OK_SECTION,
        "cast": OK_SECTION,
        "episodes": OK_SECTION,
        "reviews": OK_SECTION,
    }

    # one detail request first, then the three sub pages (reviews asks page 1)
    assert calls == [
        (BASE, None),
        (f"{BASE}/cast", None),
        (f"{BASE}/episodes", None),
        (f"{BASE}/reviews", {"page": 1}),
    ]


@pytest.mark.parametrize(
    ("suffix", "section", "code", "message"),
    [
        ("/cast", "cast", 503, "Service Unavailable"),
        ("/episodes", "episodes", 404, "Not Found"),
        (
            "/reviews",
            "reviews",
            522,
            "MyDramaList returned HTTP 522",
        ),
    ],
)
def test_full_subpage_failure_nulls_only_that_section(
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    section: str,
    code: int,
    message: str,
) -> None:
    calls = install_fake_get(monkeypatch, {suffix: code})

    r = client.get("/v2/dramas/18452-goblin/full")

    # a sub page failure must never turn the whole request into an error
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"][section] is None

    assert body["sections"][section] == {
        "status": "error",
        "http": code,
        "message": message,
    }
    # the other sections still hold real data and an ok status
    for name, status in body["sections"].items():
        if name != section:
            assert status == OK_SECTION
            assert body["data"][name] is not None

    # the failing sub page does not stop the other sub pages from being fetched
    assert len(calls) == 4
    assert (BASE, None) in calls


@pytest.mark.parametrize(
    ("outcome", "code", "message"),
    [
        (404, 404, "Not Found"),
        (502, 502, "Bad Gateway"),
        (503, 503, "Service Unavailable"),
    ],
)
def test_full_detail_failure_fails_the_whole_request(
    monkeypatch: pytest.MonkeyPatch, outcome: int, code: int, message: str
) -> None:
    calls = install_fake_get(monkeypatch, {"": outcome})

    r = client.get("/v2/dramas/18452-goblin/full")

    # same envelope as every other v2 error: no half-built payload
    assert r.status_code == code
    assert r.json() == {"success": False, "data": None, "message": message}
    # nothing beyond the required detail page may be scraped
    assert calls == [(BASE, None)]
    assert "sections" not in r.json()


def test_full_detail_transport_error_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_fake_get(monkeypatch, {"": primp.PrimpError("connection refused")})

    r = client.get("/v2/dramas/18452-goblin/full")

    assert r.status_code == 502
    message = r.json()["message"]
    assert r.json()["success"] is False
    assert r.json()["data"] is None
    assert message.endswith("Failed to reach MyDramaList. connection refused")
    assert calls == [(BASE, None)]


def test_full_detail_busy_queue_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_fake_get(monkeypatch, {"": app_client.MDLBusy()})

    r = client.get("/v2/dramas/18452-goblin/full")

    assert r.status_code == 503
    body = r.json()
    assert body == {
        "success": False,
        "data": None,
        "message": (
            "Service Unavailable. Too many requests to MyDramaList in progress, "
            "try again shortly."
        ),
    }
    assert calls == [(BASE, None)]


def test_full_subpage_transport_error_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_fake_get(monkeypatch, {"/episodes": primp.PrimpError("reset")})

    r = client.get("/v2/dramas/18452-goblin/full")

    assert r.status_code == 200
    body = r.json()
    assert body["data"]["episodes"] is None
    status = body["sections"]["episodes"]
    assert status["status"] == "error"
    assert status["http"] == 502
    assert status["message"].endswith("Failed to reach MyDramaList. reset")
    assert len(calls) == 4


def _install_limited_primp(
    monkeypatch: pytest.MonkeyPatch, delay: float
) -> tuple[list[str], dict[str, int]]:
    """Patch the underlying primp client (the layer behind client.get's
    semaphore), so every /full request still goes through the real cap and
    queue. Returns (request order, concurrency stats)."""
    stats = {"running": 0, "peak": 0}
    order: list[str] = []

    async def aread() -> bytes:
        return b"<html></html>"

    async def get(url: str, params: Any = None) -> SimpleNamespace:
        path = url.removeprefix("https://mydramalist.com/")
        order.append(path)
        stats["running"] += 1
        stats["peak"] = max(stats["peak"], stats["running"])
        await asyncio.sleep(delay)
        stats["running"] -= 1
        return SimpleNamespace(
            status_code=200, text="<html></html>", url=url, aread=aread
        )

    monkeypatch.setattr(app_client, "client", SimpleNamespace(get=get))
    return order, stats


def test_full_uses_the_shared_client_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fan-out goes through client.get's semaphore: with one slot, at most
    one MDL request is in flight at a time, and the detail page goes first."""
    # one shared instance: a fresh semaphore per call would always be free
    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(app_client, "_limit", lambda: sem)
    order, stats = _install_limited_primp(monkeypatch, 0.02)

    r = client.get("/v2/dramas/18452-goblin/full")

    assert r.status_code == 200
    assert stats["peak"] == 1
    assert order[0] == BASE
    assert len(order) == 4


def test_full_subpages_run_concurrently_when_slots_allow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With enough slots the three sub pages overlap (detail still first)."""
    sem = asyncio.Semaphore(8)
    monkeypatch.setattr(app_client, "_limit", lambda: sem)
    order, stats = _install_limited_primp(monkeypatch, 0.02)

    r = client.get("/v2/dramas/18452-goblin/full")

    assert r.status_code == 200
    assert order[0] == BASE
    # detail finishes and releases its slot, then three sub pages run at once
    assert stats["peak"] == 3
