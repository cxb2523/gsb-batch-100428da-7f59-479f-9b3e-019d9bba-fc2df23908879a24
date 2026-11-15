from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from yarl import URL

from qcrawl.utils.url import normalize_url

if TYPE_CHECKING:
    from qcrawl.core.request import Request


class RequestFingerprinter:
    """Generate unique fingerprints for requests to enable smart deduplication.

    Features:
      - ignore_query_params: set of param names to remove
      - keep_query_params: whitelist mode (only these params are kept)
      - Full URL normalization via your existing normalize_url()
      - Method + normalized URL + body hashing
      - Retry attempts are distinct: a request carrying `meta['retry_count'] > 0`
        fingerprints differently, so a retry rescheduled for an already-seen URL
        is not deduplicated away by the scheduler or a deduplicating queue.
      - blake2b (fast) or any hashlib algorithm
    """

    def __init__(
        self,
        ignore_query_params: set[str] | None = None,
        keep_query_params: set[str] | None = None,
    ):
        self.ignore_query_params = ignore_query_params or set()
        self.keep_query_params = keep_query_params or set()

        if self.keep_query_params and self.ignore_query_params:
            raise ValueError(
                "Cannot use both ignore_query_params and keep_query_params simultaneously"
            )

    def fingerprint_bytes(
        self,
        request: Request,
        digest_size: int = 16,
        algorithm: str = "blake2b",
    ) -> bytes:
        """Generate unique fingerprint for request using the serialized bytes
        returned by Request.to_bytes().

        Retries: `meta['retry_count']` (when a non-bool positive int) is mixed
        into the digest. A retry of an already-seen URL therefore gets a new
        fingerprint and is not deduplicated away — its retry delay can run.

        Args:
            request: Request instance to fingerprint.
            digest_size: Number of bytes for BLAKE2b digest (only used for blake2b).
            algorithm: Hash algorithm name ('blake2b' (default), 'sha256', or any
                       name accepted by hashlib.new).
        """
        method = (getattr(request, "method", "GET") or "GET").upper()
        method_bytes = method.encode("ascii", "ignore")

        url_bytes = self._normalized_url(str(getattr(request, "url", ""))).encode("utf-8")

        body = getattr(request, "body", None)
        body_bytes = b""
        if body:
            if isinstance(body, (bytes, bytearray)):
                body_bytes = bytes(body)
            else:
                body_bytes = str(body).encode("utf-8")

        retry_bytes = b""
        retry_count = self._retry_count(request)
        if retry_count:
            retry_bytes = f"retry:{retry_count}".encode("ascii")

        data = b"\x00".join(filter(None, [method_bytes, url_bytes, body_bytes, retry_bytes]))

        algo = (algorithm or "blake2b").lower()
        if algo == "blake2b":
            return hashlib.blake2b(data, digest_size=digest_size).digest()
        return hashlib.new(algo, data).digest()

    @staticmethod
    def _retry_count(request: Request) -> int:
        """Return the request's retry attempt, or 0 when absent/invalid.

        Deduplication must never fail a crawl, so malformed values (str, bool,
        negative) are treated as "not a retry" rather than raising.
        """
        meta = getattr(request, "meta", None)
        if not isinstance(meta, dict):
            return 0
        value = meta.get("retry_count")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return 0

    def _filter_query_params(self, url: str) -> str:
        """Filter query parameters using yarl.URL before final normalization."""
        u = URL(url)
        if not u.query:
            return url

        items: list[tuple[str, str]] = list(u.query.items())
        if self.keep_query_params:
            filtered = [(k, v) for k, v in items if k in self.keep_query_params]
        else:
            filtered = [(k, v) for k, v in items if k not in self.ignore_query_params]

        return str(u.with_query(filtered))

    def _normalized_url(self, url: str) -> str:
        """Apply query filtering + full canonical normalization."""
        return normalize_url(self._filter_query_params(url))
