import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import primp
from bs4 import BeautifulSoup

from app import client
from app.v2 import parsers
from app.v2.errors import MDLError
from app.v2.parsers import error_info


BUSY = "Too many requests to MyDramaList in progress, try again shortly."


async def _fetch(
    path: str, params: Mapping[str, Any] | None = None
) -> primp.AsyncResponse:
    """One MDL page through the shared client (concurrency cap, queue timeout)."""
    try:
        return await client.get(path, params=params)
    except primp.PrimpError as e:
        raise MDLError(502, "Failed to reach MyDramaList", str(e)) from e
    except client.MDLBusy as e:
        raise MDLError(503, info=BUSY) from e


def _check(resp: primp.AsyncResponse) -> BeautifulSoup:
    soup = BeautifulSoup(resp.text, "lxml")

    if resp.status_code != 200:
        # MDL answers unknown drama ids with an empty 500 instead of a 404 page
        empty_500 = resp.status_code == 500 and not resp.text.strip()
        raise MDLError(404 if empty_500 else resp.status_code, **error_info(soup))

    return soup


async def scrape(
    path: str,
    parse: Callable[[BeautifulSoup], dict[str, Any]],
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resp = await _fetch(path, params)
    soup = _check(resp)

    return {
        "success": True,
        "data": parse(soup),
        "link": resp.url,
        "scraped_at": datetime.now(UTC),
    }


# slug -> (sub path, parser, query params) for the three pages /full fans out to
_SubPage = tuple[
    str, Callable[[BeautifulSoup], dict[str, Any]], Mapping[str, Any] | None
]


def _sub_pages(base: str) -> list[tuple[str, _SubPage]]:
    return [
        ("cast", (f"{base}/cast", parsers.parse_cast, None)),
        ("episodes", (f"{base}/episodes", parsers.parse_episodes, None)),
        ("reviews", (f"{base}/reviews", parsers.parse_reviews, {"page": 1})),
    ]


async def _scrape_section(
    spec: _SubPage,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """One sub page: either (parsed data, an `ok` status) or (None, an `error`
    status). Only scrape-level failures are contained here; anything else stays a
    whole-request 500, the same as on the standalone endpoints."""
    path, parse, params = spec
    try:
        resp = await _fetch(path, params)
        soup = _check(resp)
    except MDLError as e:
        return None, {"status": "error", "http": e.code, "message": e.message}

    return parse(soup), {"status": "ok", "http": 200, "message": "OK"}


async def scrape_full(base: str) -> dict[str, Any]:
    """Detail page plus its cast, episodes and first reviews page.

    The detail page is required: any failure raises, so the request fails with the
    normal v2 error envelope. Only after it succeeds do the sub pages run, together
    and through the same client limits; a sub page failure nulls just that section.
    """
    detail_resp = await _fetch(base)
    detail_soup = _check(detail_resp)
    # the envelope timestamp belongs to the required detail scrape
    scraped_at = datetime.now(UTC)

    pages = _sub_pages(base)
    results = await asyncio.gather(*(_scrape_section(spec) for _, spec in pages))

    data: dict[str, Any] = {"details": parsers.parse_drama(detail_soup)}
    sections: dict[str, Any] = {
        "details": {"status": "ok", "http": 200, "message": "OK"}
    }
    for (name, _), (section_data, status) in zip(pages, results, strict=True):
        data[name] = section_data
        sections[name] = status

    return {
        "success": True,
        "data": data,
        "sections": sections,
        "link": detail_resp.url,
        "scraped_at": scraped_at,
    }
