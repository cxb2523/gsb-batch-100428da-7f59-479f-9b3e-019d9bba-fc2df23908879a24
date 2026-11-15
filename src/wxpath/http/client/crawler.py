import aiohttp

try:
    from aiohttp_client_cache import CachedSession
except ImportError:
    CachedSession = None

import asyncio
import time
import urllib.parse
from collections import defaultdict
from socket import gaierror
from typing import AsyncIterator

from wxpath.http.client.cache import get_cache_backend
from wxpath.http.client.request import Request
from wxpath.http.client.response import Response
from wxpath.http.policy.retry import RetryPolicy
from wxpath.http.policy.robots import RobotsTxtPolicy
from wxpath.http.policy.throttler import AbstractThrottler, AutoThrottler
from wxpath.http.stats import CrawlerStats, build_trace_config
from wxpath.settings import SETTINGS
from wxpath.util.logging import get_logger

log = get_logger(__name__)

CACHE_SETTINGS = SETTINGS.http.client.cache
CRAWLER_SETTINGS = SETTINGS.http.client.crawler

def get_async_session(
        headers: dict | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
        connector: aiohttp.TCPConnector | None = None,
        trace_config: aiohttp.TraceConfig | None = None
) -> aiohttp.ClientSession:
    """
    Create and return a new aiohttp session. If aiohttp-client-cache is available
    and enabled, return a new CachedSession bound to the configured SQLite backend.
    The caller is responsible for closing the session.
    """

    if timeout is None:
        timeout = aiohttp.ClientTimeout(total=CRAWLER_SETTINGS.timeout)

    if CACHE_SETTINGS.enabled and CachedSession:
        log.info("using aiohttp-client-cache")
        return CachedSession(
            cache=get_cache_backend(),
            headers=headers,
            timeout=timeout,
            connector=connector,
            trace_configs=[trace_config] if trace_config is not None else None
        )

    return aiohttp.ClientSession(
        headers=headers, 
        timeout=timeout, 
        connector=connector, 
        trace_configs=[trace_config] if trace_config is not None else None
    )


class Crawler:
    """Concurrent HTTP crawler that manages throttling, retries, and robots."""

    def __init__(
        self,
        concurrency: int = None,
        per_host: int = None,
        timeout: int = None,
        *,
        headers: dict | None = None,
        proxies: dict | None = None,
        verify_ssl: bool | None = None,
        retry_policy: RetryPolicy | None = None,
        throttler: AbstractThrottler | None = None,
        auto_throttle_target_concurrency: float = None,
        auto_throttle_start_delay: float = None,
        auto_throttle_max_delay: float = None,
        respect_robots: bool | None = None,
    ):
        cfg = CRAWLER_SETTINGS

        self.concurrency = concurrency if concurrency is not None else cfg.concurrency
        self.per_host = per_host if per_host is not None else cfg.per_host
        self._verify_ssl = verify_ssl if verify_ssl is not None else getattr(
            cfg, "verify_ssl", True
        )

        timeout = timeout if timeout is not None else cfg.timeout
        self._timeout = aiohttp.ClientTimeout(total=timeout)

        self._headers = cfg.headers | (headers or {}) # merge headers
        
        _proxies = proxies if proxies is not None else cfg.proxies
        self._proxies = _proxies if (isinstance(_proxies, defaultdict) or _proxies) else {}
        
        self.retry_policy = retry_policy or RetryPolicy()

        # auto-throttle defaults
        auto_throttle_target_concurrency = auto_throttle_target_concurrency \
            if auto_throttle_target_concurrency is not None \
            else cfg.auto_throttle_target_concurrency
        
        auto_throttle_start_delay = auto_throttle_start_delay \
            if auto_throttle_start_delay is not None \
            else cfg.auto_throttle_start_delay

        auto_throttle_max_delay = auto_throttle_max_delay \
            if auto_throttle_max_delay is not None \
            else cfg.auto_throttle_max_delay

        self.throttler = throttler or AutoThrottler(
            target_concurrency=auto_throttle_target_concurrency or self.concurrency/4.0,
            start_delay=auto_throttle_start_delay,
            max_delay=auto_throttle_max_delay,
        )

        self._sem_global = asyncio.Semaphore(self.concurrency)
        self._sem_host = defaultdict(lambda: asyncio.Semaphore(self.per_host))

        self._pending: asyncio.Queue[Request] = asyncio.Queue()
        self._results: asyncio.Queue[Response] = asyncio.Queue()

        self._session: aiohttp.ClientSession | None = None
        self._workers: list[asyncio.Task] = []
        self._retry_tasks: set[asyncio.Task] = set()
        self._closed = False
        self._stats = CrawlerStats()

        self.respect_robots = respect_robots if respect_robots is not None else cfg.respect_robots
        self._robots_policy: RobotsTxtPolicy | None = None

        # WARN: If SQLiteBackend caching is enabled and min(concurrency, per_host) > 1,
        #       write-contention is likely to occur.
        if (CACHE_SETTINGS.enabled 
            and CACHE_SETTINGS.backend == "sqlite"
            and min(self.concurrency, self.per_host) > 1
            ):
            log.warning(
                "SQLiteBackend caching is enabled and min(concurrency, per_host) > 1. "
                "Write-contention is likely to occur. Consider using RedisBackend."
            )

    def build_session(self) -> aiohttp.ClientSession:
        """Construct an `aiohttp.ClientSession` with tracing and pooling."""
        trace_config = build_trace_config(self._stats)
        # Need to build the connector as late as possible as it requires the loop
        connector = aiohttp.TCPConnector(
            limit=self.concurrency * 2,
            ttl_dns_cache=300,
            ssl=self._verify_ssl,
        )
        return get_async_session(
            headers=self._headers,
            timeout=self._timeout,
            connector=connector,
            trace_config=trace_config
        )

    async def __aenter__(self) -> "Crawler":
        """Initialize HTTP session and start background workers."""
        if self._session is None:
            # self._session = aiohttp.ClientSession(timeout=self._timeout)
            self._session = self.build_session()

        # Note: Set robots policy after session is created
        if self.respect_robots:
            self._robots_policy = RobotsTxtPolicy(self._session)

        self._workers = [
            asyncio.create_task(self._worker(), name=f"crawler-worker-{i}")
            for i in range(self.concurrency)
        ]
        return self

    async def __aexit__(self, *_) -> None:
        """Tear down workers and close the HTTP session."""
        self._closed = True
        for w in self._workers:
            w.cancel()
        for t in self._retry_tasks:
            t.cancel()

        await asyncio.gather(*self._workers, *self._retry_tasks, return_exceptions=True)

        if self._session:
            await self._session.close()

    def submit(self, req: Request) -> None:
        """Queue a request for fetching or raise if crawler already closed."""
        if self._closed:
            raise RuntimeError("crawler is closed")
        self._pending.put_nowait(req)

    def __aiter__(self) -> AsyncIterator[Response]:
        return self._result_iter()

    async def _result_iter(self) -> AsyncIterator[Response]:
        """Async iterator yielding responses as workers produce them."""
        # while not self._closed:
        while not (self._closed and self._results.empty()):
            resp = await self._results.get()
            self._results.task_done()
            yield resp

    def _proxy_for(self, url: str) -> str | None:
        host = urllib.parse.urlsplit(url).hostname
        try:
            # bracket notation first, for defaultdicts
            value = self._proxies[host]
        except KeyError:
            value = self._proxies.get(host)
        
        if not value:
            log.debug("proxy", extra={"host": host, "value": value})
        return value

    async def _worker(self) -> None:
        """Worker loop that fetches pending requests and enqueues results."""
        while True:
            req = await self._pending.get()
            try:
                resp = await self._fetch_one(req)
                if resp is not None:
                    await self._results.put(resp)

            except asyncio.CancelledError:
                # Must propagate cancellation
                log.debug("cancelled error", extra={"url": req.url})
                raise

            except gaierror as exc:
                # DNS failure: surface a terminal error response instead of
                # silently dropping the request.
                log.warning("DNS error", extra={"url": req.url})
                await self._results.put(Response(req, 0, b"", error=exc))

            except Exception as exc:
                log.warning("exception", extra={"url": req.url})
                # Last-resort safety: never drop a request silently
                await self._results.put(Response(req, 0, b"", error=exc))
            finally:
                self._pending.task_done()

    async def _fetch_one(self, req: Request) -> Response | None:
        """Fetch a single request, handling robots, throttling, and retries."""
        host = req.hostname

        if self._robots_policy:
            can_fetch = await self._robots_policy.can_fetch(
                req.url, self._headers.get("User-Agent")
            )
            if not can_fetch:
                log.debug("disallowed by robots.txt", extra={"url": req.url})
                return Response(req, 403, b"", error=RuntimeError("Disallowed by robots.txt"))

        # TODO: Move this filter to hooks
        if req.url.lower().endswith((".pdf", ".zip", ".exe")):
            req.max_retries = 0

        async with self._sem_global, self._sem_host[host]:
            t0 = asyncio.get_running_loop().time()
            await self.throttler.wait(host)
            dt = asyncio.get_running_loop().time() - t0

            self._stats.throttle_waits += 1
            self._stats.throttle_wait_time += dt
            self._stats.throttle_waits_by_host[host] += 1

            start = time.monotonic()
            try:
                log.info("fetching", extra={"url": req.url})
                async with self._session.get(
                    req.url,
                    headers=self._headers | req.headers,
                    proxy=self._proxy_for(req.url),
                    timeout=req.timeout or self._timeout,
                ) as resp:
                    from_cache = getattr(resp, "from_cache", False)
                    if from_cache:
                        # NOTE: This is a bit of a hack, but it works. aiohttp-client-cache does not
                        #  interface with TraceConfigs on cache hit, so we have to do it here.
                        self._stats.requests_cache_hit += 1
                        log.info("[CACHE HIT]", extra={"req.url": req.url, "resp.url": resp.url})
                    else:
                        log.info("[CACHE MISS]", extra={"req.url": req.url, "resp.url": resp.url})

                    _start = time.monotonic()
                    body = await resp.read()

                    end = time.monotonic()
                    latency = end - _start
                    self.throttler.record_latency(host, latency)

                    if self.retry_policy.should_retry(req, response=resp):
                        self._schedule_retry(req)
                        return None

                    if resp.status in self.retry_policy.retry_statuses:
                        # Retryable status, but retries are exhausted or disabled
                        # (max_retries reached / dont_retry): emit a terminal
                        # error response so the request never vanishes silently.
                        err = RuntimeError(
                            f"retries exhausted for {req.url}: "
                            f"last response status {resp.status}"
                        )
                        log.error(
                            "retries exhausted",
                            extra={"url": req.url, "status": resp.status},
                        )
                        return Response(req, 0, b"", error=err,
                                        request_start=_start, response_end=end)

                    return Response(req, resp.status, body, dict(resp.headers),
                                    request_start=_start, response_end=end)
            except asyncio.CancelledError:
                # Normal during shutdown / timeout propagation
                log.debug("cancelled error", extra={"url": req.url})
                raise
            except Exception as exc:
                end = time.monotonic()
                latency = end - start
                self.throttler.record_latency(host, latency)

                if self.retry_policy.should_retry(req, exception=exc):
                    self._schedule_retry(req)
                    return None
                
                log.error("request failed", extra={"url": req.url}, exc_info=exc)
                return Response(req, 0, b"", error=exc, request_start=start, response_end=end)

    def _schedule_retry(self, req: Request) -> None:
        """Reschedule a request according to the retry policy.

        The backoff delay is awaited in a detached task so the worker is
        freed immediately and other requests keep flowing. The retried
        request re-enters the pending queue and is fetched through the
        concurrency semaphores and throttler like any other request.
        """
        req.retries += 1
        delay = self.retry_policy.get_delay(req)

        log.warning(
            "retrying",
            extra={"url": req.url, "retry": req.retries, "delay": delay},
        )

        if delay and delay > 0:
            task = asyncio.create_task(
                self._delayed_requeue(req, delay), name="crawler-retry"
            )
            self._retry_tasks.add(task)
            task.add_done_callback(self._retry_tasks.discard)
        else:
            self._requeue(req)

    async def _delayed_requeue(self, req: Request, delay: float) -> None:
        """Wait out the backoff delay, then re-enqueue the request."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            # Shutting down: the crawler is closed and consumers are gone.
            log.debug("retry cancelled during shutdown", extra={"url": req.url})
            raise
        self._requeue(req)

    def _requeue(self, req: Request) -> None:
        """Put a retried request back on the pending queue."""
        if self._closed:
            # Never drop a request silently: emit a terminal error response.
            log.warning("crawler closed before retry", extra={"url": req.url})
            self._results.put_nowait(
                Response(req, 0, b"",
                         error=RuntimeError("crawler closed before retry"))
            )
            return
        self._pending.put_nowait(req)