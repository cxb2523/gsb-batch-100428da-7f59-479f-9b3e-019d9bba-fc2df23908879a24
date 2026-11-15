"""Async HTTP crawler — httpx + asyncio.

Minimal but production-shaped:
- async with bounded concurrency
- robots.txt honored (urllib.robotparser, in-memory cache per host)
- depth limit
- same-host filter (configurable)
- max URLs limit
- redirect tracking (httpx follows; we count hops)

This is intentionally NOT Scrapy. Scrapy adds 500 lines of config for
features we don't need yet. We can plug Scrapy as a second backend later
without changing extract.py or storage/.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from crawlforge.engine.extract import extract_url_row
from crawlforge.engine.normalize import canonicalize


@dataclass
class CrawlConfig:
    start_url: str
    max_urls: int = 500
    max_depth: int = 10
    concurrency: int = 8
    same_host_only: bool = True
    user_agent: str = "crawlforge/0.0.1 (+https://github.com/mario-hernandez/crawlforge)"
    timeout_s: float = 15.0
    respect_robots: bool = True
    persist_html: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class CrawlStats:
    urls_total: int = 0
    urls_failed: int = 0
    urls_blocked: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class _RobotsCache:
    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._cache: dict[str, RobotFileParser | None] = {}
        self._lock = asyncio.Lock()

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host_key = f"{parts.scheme}://{parts.netloc}"
        async with self._lock:
            if host_key not in self._cache:
                rp = await self._fetch(host_key)
                self._cache[host_key] = rp
            rp = self._cache[host_key]
        if rp is None:
            return True
        return rp.can_fetch(self._user_agent, url)

    async def _fetch(self, host_key: str) -> RobotFileParser | None:
        try:
            resp = await self._client.get(f"{host_key}/robots.txt")
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp


async def crawl(config: CrawlConfig) -> AsyncIterator[dict[str, Any]]:
    """Yield URLRow dicts as URLs are crawled.

    Caller writes them wherever they want (DuckDB, Parquet, JSON Lines).
    """
    start = canonicalize(config.start_url)
    start_host = urlsplit(start).netloc
    seen: set[str] = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    stats = CrawlStats(started_at=datetime.now(timezone.utc))

    headers = {
        "User-Agent": config.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        **config.extra_headers,
    }

    limits = httpx.Limits(max_keepalive_connections=config.concurrency * 2)
    async with httpx.AsyncClient(
        headers=headers,
        timeout=config.timeout_s,
        follow_redirects=True,
        limits=limits,
        http2=False,
    ) as client:
        robots = _RobotsCache(client, config.user_agent)
        sem = asyncio.Semaphore(config.concurrency)

        async def fetch_one(url: str, depth: int) -> dict[str, Any] | None:
            if config.respect_robots and not await robots.allowed(url):
                stats.urls_blocked += 1
                stats.urls_failed += 1
                return {
                    "url": url,
                    "final_url": url,
                    "status_code": 0,
                    "headers": {},
                    "body": b"",
                    "response_time_ms": 0,
                    "depth": depth,
                    "redirect_count": 0,
                    "_blocked_by_robots": True,
                }
            t0 = time.perf_counter()
            try:
                async with sem:
                    resp = await client.get(url)
            except httpx.HTTPError as e:
                stats.urls_failed += 1
                return {
                    "url": url,
                    "final_url": str(getattr(e, "request", {}).url) if getattr(e, "request", None) else url,
                    "status_code": 0,
                    "headers": {},
                    "body": b"",
                    "response_time_ms": int((time.perf_counter() - t0) * 1000),
                    "depth": depth,
                    "redirect_count": 0,
                    "_error": type(e).__name__,
                }
            return {
                "url": url,
                "final_url": str(resp.url),
                "status_code": resp.status_code,
                "headers": {k.lower(): v for k, v in resp.headers.items()},
                "body": resp.content,
                "response_time_ms": int((time.perf_counter() - t0) * 1000),
                "depth": depth,
                "redirect_count": len(resp.history),
            }

        in_flight: set[asyncio.Task[Any]] = set()

        while (queue or in_flight) and stats.urls_total < config.max_urls:
            while queue and len(in_flight) < config.concurrency and stats.urls_total + len(in_flight) < config.max_urls:
                url, depth = queue.popleft()
                in_flight.add(asyncio.create_task(fetch_one(url, depth)))

            if not in_flight:
                break

            done, in_flight = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                fetched = task.result()
                if fetched is None:
                    continue

                row = extract_url_row(
                    url=fetched["url"],
                    final_url=fetched["final_url"],
                    status_code=fetched["status_code"],
                    headers=fetched["headers"],
                    body=fetched["body"],
                    response_time_ms=fetched["response_time_ms"],
                    depth=fetched["depth"],
                    redirect_count=fetched["redirect_count"],
                    crawled_at=datetime.now(timezone.utc),
                    persist_html=config.persist_html,
                )
                if fetched.get("_blocked_by_robots"):
                    row["from_robots"] = True
                    row["is_indexable"] = False
                    row["indexability_reason"] = "blocked by robots.txt"
                elif fetched.get("_error"):
                    # Real request failure (timeout, connection refused, ...) —
                    # distinguishable from a robots block via from_robots=False.
                    row["is_indexable"] = False
                    row["indexability_reason"] = f"request failed: {fetched['_error']}"

                stats.urls_total += 1
                yield row

                if fetched.get("_blocked_by_robots") or fetched.get("_error"):
                    continue  # no link discovery from blocked/failed fetches

                # Enqueue the redirect target as its own page so its title,
                # canonical, etc. are captured. The original URL row above
                # keeps final_url pointing here. Bounded by the same
                # max_depth / max_urls / same_host_only rules as links, and
                # the `seen` set guarantees it is crawled at most once.
                final = row.get("final_url")
                if (
                    final
                    and final != row["url"]
                    and final not in seen
                    and row["depth"] < config.max_depth
                    and (not config.same_host_only or urlsplit(final).netloc == start_host)
                ):
                    seen.add(final)
                    queue.append((final, row["depth"] + 1))

                if row["depth"] >= config.max_depth:
                    continue
                for link in row.get("_links", []):
                    target = link["target_url"]
                    if target in seen:
                        continue
                    if config.same_host_only and urlsplit(target).netloc != start_host:
                        continue
                    seen.add(target)
                    queue.append((target, row["depth"] + 1))

        for task in in_flight:
            task.cancel()
    stats.finished_at = datetime.now(timezone.utc)
