from dataclasses import dataclass, field, fields, asdict
from typing import Any, Dict, Iterable, List, Optional, Union

from ._errors import (
    LumintuScraperError,
    AuthenticationError,
    NotFoundError,
)
from ._logging import get_logger

logger = get_logger()

# Final status of a single item inside a batch.
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"  # unfinished, e.g. result polling timed out

# Errors that will not fix themselves on a later run: the item is marked
# as permanently failed and skipped on resume.
_PERMANENT_ERRORS = (AuthenticationError, NotFoundError)

# Terminal scrape statuses reported by the result endpoint that mean "done OK".
_SUCCESS_STATUSES = ("completed", "success")


@dataclass
class BatchItemResult:
    """
    Outcome of a single product inside a batch scrape.

    Fields:
    - url / country: the input that identifies the item.
    - status: "success" | "failed" | "pending" (unfinished).
    - request_id: the actor request id, if the actor call went through.
    - result: the final result payload returned by the result endpoint
      (only set on success).
    - error / error_type: why the item failed or is still pending.
    - permanent: True when the failure is not worth retrying on resume
      (e.g. authentication / not-found errors).
    """
    url: str
    country: str
    status: str = STATUS_PENDING
    request_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    permanent: bool = False

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchItemResult":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class BatchResult:
    """
    Aggregated outcome of a batch scrape. Can be serialized with to_dict()
    and passed back as `previous` to resume an interrupted batch.
    """
    items: List[BatchItemResult] = field(default_factory=list)

    @property
    def succeeded(self) -> List[BatchItemResult]:
        return [i for i in self.items if i.status == STATUS_SUCCESS]

    @property
    def failed(self) -> List[BatchItemResult]:
        return [i for i in self.items if i.status == STATUS_FAILED]

    @property
    def unfinished(self) -> List[BatchItemResult]:
        return [i for i in self.items if i.status == STATUS_PENDING]

    @property
    def pending(self) -> List[BatchItemResult]:
        """Items a resume run would still process: not successful and not
        permanently failed. Empty when every item succeeded."""
        return [
            i for i in self.items
            if i.status != STATUS_SUCCESS and not i.permanent
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {"items": [i.to_dict() for i in self.items]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchResult":
        return cls(
            items=[BatchItemResult.from_dict(i) for i in data.get("items", [])]
        )


class BatchClient:
    """
    Batch entry point of the client. Scrapes products one by one through the
    existing actor + result polling flow, isolating failures per item so one
    bad product never aborts the whole batch.
    """

    def __init__(self, client: "Any"):
        # `client` is a LumintuScraperClient; kept untyped to avoid a cycle.
        self._client = client

    def scrape_shopee_products(
            self,
            items: Iterable[Dict[str, str]],
            previous: Union[BatchResult, Dict[str, Any], None] = None,
            actor_path: str = "shopee/scrape",
            interval: int = 2,
            max_attempts: int = 30,
            timeout: int = 60,
    ) -> BatchResult:
        """
        Scrape a batch of Shopee products.

        - items: iterable of {"url": ..., "country": ...} dicts.
        - previous: a BatchResult (or its to_dict() form) from an earlier run.
          Items that already succeeded are reused as-is without new requests;
          permanently failed items are carried over untouched; everything else
          is (re)processed.
        - interval / max_attempts: polling parameters for wait_for_result.
        - timeout: per-request HTTP timeout for the actor call and each poll.

        Never raises for per-item scrape failures: each item ends up as
        success / failed / pending with the reason recorded on the item.
        """
        previous_result = self._coerce_previous(previous)
        previous_by_key = {
            (i.url, i.country): i for i in previous_result.items
        }

        batch_items: List[BatchItemResult] = []
        for raw in items:
            url, country = self._normalize_item(raw)
            key = (url, country)
            prev = previous_by_key.get(key)

            if prev is not None and prev.status == STATUS_SUCCESS:
                logger.info(f"Reusing previous success for {url}")
                batch_items.append(prev)
                continue
            if prev is not None and prev.permanent:
                logger.info(f"Skipping permanently failed item {url}")
                batch_items.append(prev)
                continue

            batch_items.append(
                self._scrape_one(url, country, actor_path, interval,
                                 max_attempts, timeout)
            )

        return BatchResult(items=batch_items)

    @staticmethod
    def _coerce_previous(
            previous: Union[BatchResult, Dict[str, Any], None]
    ) -> BatchResult:
        if previous is None:
            return BatchResult()
        if isinstance(previous, BatchResult):
            return previous
        if isinstance(previous, dict):
            return BatchResult.from_dict(previous)
        raise LumintuScraperError(
            f"Unsupported type for previous batch result: {type(previous)}"
        )

    @staticmethod
    def _normalize_item(raw: Any) -> "tuple[str, str]":
        if isinstance(raw, dict):
            url, country = raw.get("url"), raw.get("country")
        else:
            url = getattr(raw, "url", None)
            country = getattr(raw, "country", None)
        if not url or not country:
            raise LumintuScraperError(
                f"Batch item must provide 'url' and 'country', got: {raw!r}"
            )
        return url, country

    def _scrape_one(
            self,
            url: str,
            country: str,
            actor_path: str,
            interval: int,
            max_attempts: int,
            timeout: int,
    ) -> BatchItemResult:
        item = BatchItemResult(url=url, country=country)
        try:
            actor_resp = self._client.actor(actor_path).call(
                {"url": url, "country": country}, timeout=timeout
            )
            item.request_id = actor_resp.get("request_id")

            final = self._client.result().wait_for_result(
                actor_resp,
                interval=interval,
                max_attempts=max_attempts,
                timeout=timeout,
                as_dict=True,
            )

            if final.get("status") in _SUCCESS_STATUSES:
                item.status = STATUS_SUCCESS
                item.result = final
            else:
                item.status = STATUS_FAILED
                item.error = (
                    final.get("message")
                    or f"Scrape finished with status: {final.get('status')}"
                )
                item.error_type = "ScrapeFailed"
        except TimeoutError as e:
            # Result never became ready: unfinished, safe to retry on resume.
            item.status = STATUS_PENDING
            item.error = str(e)
            item.error_type = type(e).__name__
        except _PERMANENT_ERRORS as e:
            item.status = STATUS_FAILED
            item.error = str(e)
            item.error_type = type(e).__name__
            item.permanent = True
        except LumintuScraperError as e:
            # RateLimitError / ServerError / RequestError after the existing
            # retry+backoff policy is exhausted: failed, resumable.
            item.status = STATUS_FAILED
            item.error = str(e)
            item.error_type = type(e).__name__
        except Exception as e:  # noqa: BLE001 - one bad item must not abort the batch
            item.status = STATUS_FAILED
            item.error = str(e)
            item.error_type = type(e).__name__

        if item.status != STATUS_SUCCESS:
            logger.warning(
                f"Batch item {url} ended as {item.status}: {item.error}"
            )
        return item
