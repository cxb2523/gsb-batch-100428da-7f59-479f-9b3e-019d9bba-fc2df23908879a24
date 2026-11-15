# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html

import base64
import hashlib
import logging
import threading

from fake_useragent import UserAgent
from scrapy import signals
from scrapy.core.downloader.handlers.http11 import TunnelError
from twisted.internet.error import (
    ConnectError,
    ConnectionDone,
    ConnectionLost,
    ConnectionRefusedError,
    DNSLookupError,
    TCPTimedOutError,
    TimeoutError,
)

from scrapy_utils.settings import proxyAuth

logger = logging.getLogger(__name__)

# Default proxy entry point. Can be overridden with the PROXY_URL setting.
DEFAULT_PROXY_URL = "https://http-dyn.abuyun.com:9020"

# Exceptions that indicate the currently selected proxy credential / tunnel is
# unusable. Mirrors the failure classes handled by Scrapy's RetryMiddleware.
PROXY_FAILURE_EXCEPTIONS = (
    ConnectError,
    ConnectionRefusedError,
    ConnectionLost,
    ConnectionDone,
    DNSLookupError,
    TimeoutError,
    TCPTimedOutError,
    TunnelError,
)


def mask_credential(credential):
    """Return a non-reversible, human-checkable identifier for a credential.

    The full credential (``user:password``) must never appear in logs. We keep
    the first two characters of the username as a hint and append a short hash
    so different credentials can still be told apart.
    """
    credential = str(credential)
    username = credential.split(":", 1)[0]
    prefix = username[:2]
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}***{digest}"


class ProxyCredentialPool:
    """Stateful, round-robin proxy credential pool for one crawl process.

    Credentials are handed out in order and cycled. A credential that returns
    HTTP 407 or raises a proxy connection exception is marked unavailable for
    the rest of the run; later requests never select it again. When every
    credential has been disabled the pool reports that the caller should fall
    back to a direct (no-proxy) connection.
    """

    def __init__(self, credentials):
        # Preserve ordering but drop blank / whitespace-only entries;
        # duplicates are kept so the configured sequence stays auditable.
        cleaned = []
        for credential in credentials or []:
            credential = str(credential).strip()
            if credential:
                cleaned.append(credential)
        self._credentials = cleaned
        self._size = len(cleaned)
        self._disabled = set()
        self._cursor = 0
        self._lock = threading.Lock()
        self._all_disabled_warned = False

    @property
    def size(self):
        """Total number of configured credentials (including disabled ones)."""
        return self._size

    def available_count(self):
        return self._size - len(self._disabled)

    def is_empty(self):
        return self._size == 0

    def is_exhausted(self):
        """True when credentials were configured but none remain usable."""
        return self._size > 0 and self.available_count() == 0

    def acquire(self):
        """Return ``(index, credential)`` for the next usable credential.

        Returns ``(None, None)`` when the list is empty or every credential has
        been disabled, in which case the caller must connect directly.
        """
        with self._lock:
            if self.available_count() == 0:
                return None, None
            # Advance the cursor until it points at an enabled credential.
            while self._cursor in self._disabled:
                self._cursor = (self._cursor + 1) % self._size
            index = self._cursor
            credential = self._credentials[index]
            self._cursor = (self._cursor + 1) % self._size
            return index, credential

    def mark_unavailable(self, index):
        """Disable a credential for the remainder of the run.

        Returns ``True`` if this call performed the transition, ``False`` when
        the index was unknown or already disabled (makes retries idempotent).
        """
        with self._lock:
            if index is None or not (0 <= index < self._size):
                return False
            if index in self._disabled:
                return False
            self._disabled.add(index)
            exhausted = self.available_count() == 0
            if exhausted and not self._all_disabled_warned:
                self._all_disabled_warned = True
            return True

    def take_all_disabled_warning(self):
        """Consume the one-shot 'all credentials disabled' warning flag."""
        with self._lock:
            if self._all_disabled_warned:
                self._all_disabled_warned = False
                return True
            return False

    def masked_id(self, index):
        if index is None or not (0 <= index < self._size):
            return "none"
        return mask_credential(self._credentials[index])


# useful for handling different item types with a single interface


class ScrapyUtilsSpiderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the spider middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response, spider):
        # Called for each response that goes through the spider
        # middleware and into the spider.

        # Should return None or raise an exception.
        return None

    def process_spider_output(self, response, result, spider):
        # Called with the results returned from the Spider, after
        # it has processed the response.

        # Must return an iterable of Request, or item objects.
        yield from result

    def process_spider_exception(self, response, exception, spider):
        # Called when a spider or process_spider_input() method
        # (from other spider middleware) raises an exception.

        # Should return either None or an iterable of Request or item objects.
        pass

    def process_start_requests(self, start_requests, spider):
        # Called with the start requests of the spider, and works
        # similarly to the process_spider_output() method, except
        # that it doesn’t have a response associated.

        # Must return only requests (not items).
        yield from start_requests

    def spider_opened(self, spider):
        spider.logger.info(f"Spider opened: {spider.name}")


class ScrapyUtilsDownloaderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the downloader middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        # Called for each request that goes through the downloader
        # middleware.

        # Must either:
        # - return None: continue processing this request
        # - or return a Response object
        # - or return a Request object
        # - or raise IgnoreRequest: process_exception() methods of
        #   installed downloader middleware will be called
        return None

    def process_response(self, request, response, spider):
        # Called with the response returned from the downloader.

        # Must either;
        # - return a Response object
        # - or return a Request object
        # - or raise IgnoreRequest
        return response

    def process_exception(self, request, exception, spider):
        # Called when a download handler or a process_request()
        # (from other downloader middleware) raises an exception.

        # Must either:
        # - return None: continue processing this exception
        # - return a Response object: stops process_exception() chain
        # - return a Request object: stops process_exception() chain
        pass

    def spider_opened(self, spider):
        spider.logger.info(f"Spider opened: {spider.name}")


class RandomUserAgentMiddleware:
    @staticmethod
    def process_request(request, spider):
        request.headers.setdefault("User-Agent", UserAgent(verify_ssl=False).random)


class HttpProxyMiddleware(object):
    """Downloader middleware with auditable, stateful proxy credential rotation.

    Behaviour:
      * Credentials are rotated in order (round-robin) within the process.
      * An empty credential list means no proxy headers are written and the
        request is sent directly, with one explicit warning.
      * HTTP 407 responses and proxy connection exceptions disable the
        offending credential for the rest of the run; retries pick the next
        available credential instead of reusing the failed one.
      * Once every credential is disabled, requests fall back to a direct
        connection and a single summary warning is emitted.
    """

    def __init__(self, credentials=None, proxy_url=None, max_retries=None):
        self.pool = ProxyCredentialPool(
            proxyAuth if credentials is None else credentials
        )
        self.proxy_url = proxy_url or DEFAULT_PROXY_URL
        # A single request gets at most one attempt per configured credential.
        self.max_retries = (
            self.pool.size if max_retries is None else max_retries
        )
        self._empty_warned = False

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            credentials=crawler.settings.getlist("proxyAuth", proxyAuth),
            proxy_url=crawler.settings.get("PROXY_URL", DEFAULT_PROXY_URL),
        )

    @staticmethod
    def _log(spider, level, message):
        """Prefer the spider logger; fall back to the module logger."""
        target = getattr(spider, "logger", None) or logger
        getattr(target, level)(message)

    @staticmethod
    def _clear_proxy_headers(request):
        for header in ("Proxy-Authorization", "Proxy-Connection", "Connection"):
            request.headers.pop(header, None)
        request.meta.pop("proxy", None)

    def process_request(self, request, spider):
        attempts = request.meta.get("proxy_retry_times", 0)
        index, credential = self.pool.acquire()

        if index is None:
            # No credentials configured, or every credential has been disabled.
            self._clear_proxy_headers(request)
            if self.pool.is_empty() and not self._empty_warned:
                self._empty_warned = True
                self._log(
                    spider,
                    "warning",
                    "proxyAuth credential list is empty; sending request "
                    f"{request.url} directly without Proxy-Authorization",
                )
            elif self.pool.take_all_disabled_warning():
                self._log(
                    spider,
                    "warning",
                    "all proxy credentials have been marked unavailable "
                    f"({self.pool.size} total); falling back to direct "
                    f"connection for {request.url}",
                )
            return None

        if attempts >= self.max_retries:
            # Retry budget exhausted (e.g. every credential failed with 407):
            # do not keep cycling forever, send directly.
            self._clear_proxy_headers(request)
            self._log(
                spider,
                "warning",
                f"proxy retry budget exhausted after {attempts} attempt(s); "
                f"sending {request.url} directly",
            )
            return None

        request.headers["Proxy-Authorization"] = (
            "Basic "
            + base64.urlsafe_b64encode(bytes(credential, "ascii")).decode("utf8")
        )
        request.meta["proxy"] = self.proxy_url
        request.meta["proxy_credential_index"] = index
        request.headers["Proxy-Connection"] = "close"
        request.headers["Connection"] = "close"
        self._log(
            spider,
            "info",
            f"selected proxy credential #{index} "
            f"({self.pool.masked_id(index)}); "
            f"{self.pool.available_count()} credential(s) remaining; "
            f"request={request.url}",
        )
        return None

    def _disable(self, index, reason, request, spider):
        """Mark a credential unusable and emit an auditable failure log."""
        changed = self.pool.mark_unavailable(index)
        self._log(
            spider,
            "warning",
            f"proxy credential #{index} ({self.pool.masked_id(index)}) marked "
            f"unavailable: {reason}; "
            f"{self.pool.available_count()} credential(s) remaining; "
            f"request={request.url}",
        )
        if self.pool.take_all_disabled_warning():
            self._log(
                spider,
                "warning",
                "all proxy credentials have been marked unavailable "
                f"({self.pool.size} total); subsequent requests will be sent "
                "directly without proxy",
            )
        return changed

    @staticmethod
    def _retry(request):
        """Build a replacement request that picks the next credential."""
        new_request = request.copy()
        new_request.dont_filter = True
        new_request.meta["proxy_retry_times"] = (
            request.meta.get("proxy_retry_times", 0) + 1
        )
        # Drop the failed credential's state; process_request re-selects one.
        new_request.meta.pop("proxy_credential_index", None)
        return new_request

    def process_response(self, request, response, spider):
        if response.status != 407:
            return response

        index = request.meta.get("proxy_credential_index")
        if index is None:
            # 407 on a direct request cannot be blamed on a credential; let the
            # rest of the middleware chain / spider handle the response.
            return response

        self._disable(index, "HTTP 407 Proxy Authentication Required", request, spider)
        attempts = request.meta.get("proxy_retry_times", 0)
        if attempts < self.max_retries:
            self._log(
                spider,
                "info",
                f"retrying {request.url} with the next available proxy "
                f"credential (attempt {attempts + 1}/{self.max_retries})",
            )
            return self._retry(request)

        self._log(
            spider,
            "warning",
            f"no proxy retries left for {request.url} after HTTP 407; "
            "returning the response to the spider",
        )
        return response

    def process_exception(self, request, exception, spider):
        index = request.meta.get("proxy_credential_index")
        if index is None or not isinstance(exception, PROXY_FAILURE_EXCEPTIONS):
            # Direct request or an unrelated failure: let Scrapy's normal
            # retry / error handling take over.
            return None

        self._disable(index, f"{type(exception).__name__}: {exception}", request, spider)
        attempts = request.meta.get("proxy_retry_times", 0)
        if attempts < self.max_retries:
            self._log(
                spider,
                "info",
                f"retrying {request.url} with the next available proxy "
                f"credential after connection error "
                f"(attempt {attempts + 1}/{self.max_retries})",
            )
            return self._retry(request)

        # Retry budget exhausted: re-raise through the normal chain so the
        # request is handled as a genuine failure rather than looping forever.
        self._log(
            spider,
            "warning",
            f"no proxy retries left for {request.url} after connection error; "
            "propagating the exception",
        )
        return None
