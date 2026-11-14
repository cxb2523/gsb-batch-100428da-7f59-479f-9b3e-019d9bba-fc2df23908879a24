"""
    Created by howie.hu at 2018.
    Description: Per-request outcome record
    Changelog: all notable changes to this file will be documented
"""

from typing import Optional


class RequestOutcome:
    """
    Constants describing how a request finally ended.

    - ``SUCCESS``: the request succeeded on its first (only) attempt
    - ``RETRIED_SUCCESS``: the request failed at least once, then succeeded
    - ``FAILED``: retries were exhausted, the request was abandoned
    """

    SUCCESS = "success"
    RETRIED_SUCCESS = "retried_success"
    FAILED = "failed"

    ALL = (SUCCESS, RETRIED_SUCCESS, FAILED)

    # Friendly aliases, so callers can filter records with the wording they
    # are used to. They are normalised to the canonical values above.
    ALIASES = {
        "success": SUCCESS,
        "ok": SUCCESS,
        "retried_success": RETRIED_SUCCESS,
        "retry_success": RETRIED_SUCCESS,
        "retried": RETRIED_SUCCESS,
        "failed": FAILED,
        "fail": FAILED,
        "failure": FAILED,
        "abandoned": FAILED,
        "failed_abandoned": FAILED,
        "gave_up": FAILED,
    }

    @classmethod
    def normalize(cls, outcome: Optional[str]) -> Optional[str]:
        """Return the canonical outcome name for ``outcome``."""
        if outcome is None:
            return None
        return cls.ALIASES.get(str(outcome).lower(), outcome)


class RequestRecord:
    """
    The final outcome of one single Request, from entering the queue to
    being fully processed by the Spider.

    Attributes:
        url (str): Target URL of the request.
        method (str): HTTP method of the request.
        outcome (str): One of :class:`RequestOutcome` values.
        attempts (int): How many HTTP requests were actually sent for this
            request (the first try is counted as one attempt).
        status (Optional[int]): Final status code. ``-1`` means that retries
            were exhausted and no valid response was obtained.
        error (Optional[str]): Error message when the request was abandoned.
        callback (Optional[str]): Name of the callback which produced this
            request (the parent's callback for a yielded request, the
            request's own callback for a ``start_urls`` request).
        request_callback (Optional[str]): Name of the callback which handles
            this request's response.
        parent_url (Optional[str]): URL of the parent request when this
            request was yielded by a callback, otherwise ``None``.
    """

    __slots__ = (
        "url",
        "method",
        "outcome",
        "attempts",
        "status",
        "error",
        "callback",
        "request_callback",
        "parent_url",
        "_extra",
    )

    def __init__(
        self,
        url: str,
        method: str = "GET",
        *,
        outcome: str = RequestOutcome.FAILED,
        attempts: int = 0,
        status: Optional[int] = None,
        error: Optional[str] = None,
        callback: Optional[str] = None,
        request_callback: Optional[str] = None,
        parent_url: Optional[str] = None,
        **extra
    ):
        self.url = url
        self.method = method or "GET"
        self.outcome = RequestOutcome.normalize(outcome)
        self.attempts = int(attempts or 0)
        self.status = status
        self.error = error
        self.callback = callback
        self.request_callback = request_callback
        self.parent_url = parent_url
        self._extra = extra

    # Convenience aliases -------------------------------------------------
    @property
    def status_code(self) -> Optional[int]:
        """Alias of :attr:`status`."""
        return self.status

    @property
    def request_url(self) -> str:
        """Alias of :attr:`url`."""
        return self.url

    @property
    def http_method(self) -> str:
        """Alias of :attr:`method`."""
        return self.method

    @property
    def attempt_count(self) -> int:
        """Alias of :attr:`attempts`."""
        return self.attempts

    @property
    def error_message(self) -> Optional[str]:
        """Alias of :attr:`error`."""
        return self.error

    @property
    def callback_name(self) -> Optional[str]:
        """Alias of :attr:`callback`."""
        return self.callback

    @property
    def source_callback(self) -> Optional[str]:
        """Alias of :attr:`callback`."""
        return self.callback

    @property
    def parent_callback(self) -> Optional[str]:
        """Alias of :attr:`callback`."""
        return self.callback

    @property
    def handler_callback(self) -> Optional[str]:
        """Alias of :attr:`request_callback`."""
        return self.request_callback

    @property
    def parent_request_url(self) -> Optional[str]:
        """Alias of :attr:`parent_url`."""
        return self.parent_url

    @property
    def succeeded(self) -> bool:
        """Whether the request finally succeeded, retries included."""
        return self.outcome in (RequestOutcome.SUCCESS, RequestOutcome.RETRIED_SUCCESS)

    @property
    def retried(self) -> bool:
        """Whether the request failed at least once before succeeding."""
        return self.outcome == RequestOutcome.RETRIED_SUCCESS

    # Mapping-like access ----------------------------------------------------
    def to_dict(self) -> dict:
        """Return a plain ``dict`` representation of this record."""
        data = {
            "url": self.url,
            "method": self.method,
            "outcome": self.outcome,
            "attempts": self.attempts,
            "status": self.status,
            "status_code": self.status,
            "error": self.error,
            "callback": self.callback,
            "request_callback": self.request_callback,
            "parent_url": self.parent_url,
        }
        data.update(self._extra)
        return data

    as_dict = to_dict

    def get(self, key, default=None):
        """``dict``-like ``get``."""
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return self.to_dict().keys()

    def items(self):
        return self.to_dict().items()

    def __getitem__(self, key):
        if key in self.__slots__:
            value = getattr(self, key)
            return value
        try:
            return self._extra[key]
        except KeyError:
            raise KeyError(key)

    def __contains__(self, key):
        return key in self.__slots__ or key in self._extra

    def __repr__(self):
        return (
            f"<RequestRecord {self.method} {self.url} outcome={self.outcome} "
            f"attempts={self.attempts} status={self.status}>"
        )


class RequestRecordStore:
    """
    An ordered container of :class:`RequestRecord` objects.

    It behaves like a read-only list of records and offers convenience
    queries by URL and by outcome. It is safe to use with multiple worker
    coroutines: every mutation happens synchronously in the event loop.
    """

    def __init__(self):
        self._records = []
        self._by_url = {}

    # Collection protocol ---------------------------------------------------
    def add(self, record: RequestRecord) -> RequestRecord:
        """Append a record and index it by URL"""
        self._records.append(record)
        self._by_url.setdefault(record.url, []).append(record)
        return record

    append = add

    def clear(self):
        """Drop every record"""
        self._records = []
        self._by_url = {}

    def __len__(self):
        return len(self._records)

    def __iter__(self):
        return iter(self._records)

    def __getitem__(self, item):
        if isinstance(item, str):
            return self._by_url.get(item, [])
        return self._records[item]

    def __bool__(self):
        return bool(self._records)

    def __repr__(self):
        return f"<RequestRecordStore records={len(self._records)}>"

    @property
    def records(self) -> list:
        """All records in insertion order"""
        return list(self._records)

    def get(self, url: str, default=None):
        """
        Return the last record of ``url``, or ``default`` when the URL has
        never been processed.
        """
        records = self._by_url.get(url)
        if not records:
            return default
        return records[-1]

    def get_all(self, url: str) -> list:
        """Return every record of ``url``"""
        return list(self._by_url.get(url, []))

    def find(self, url: str = None, outcome: str = None, method: str = None) -> list:
        """Return the records matching all the given criteria"""
        outcome = RequestOutcome.normalize(outcome)
        if url is not None:
            result = list(self._by_url.get(url, []))
        else:
            result = list(self._records)
        if outcome is not None:
            result = [record for record in result if record.outcome == outcome]
        if method is not None:
            method = method.upper()
            result = [record for record in result if record.method == method]
        return result

    query = find

    def count(self, outcome: str = None, url: str = None) -> int:
        """Count the records matching ``outcome`` (and optionally ``url``)"""
        return len(self.find(url=url, outcome=outcome))

    def counts(self) -> dict:
        """
        Return the number of records per outcome.

        The result always contains the three canonical outcome keys. Their
        values are consistent with ``Spider.success_counts`` and
        ``Spider.failed_counts``: successful records (including the ones
        which succeeded after retries) match ``success_counts``, while
        abandoned records match ``failed_counts``.
        """
        counts = {outcome: 0 for outcome in RequestOutcome.ALL}
        for record in self._records:
            counts[record.outcome] = counts.get(record.outcome, 0) + 1
        return counts

    summary = counts

    def outcome_counts(self) -> dict:
        """Alias of :meth:`counts`"""
        return self.counts()

    def to_list(self) -> list:
        """Return every record as a plain ``dict``"""
        return [record.to_dict() for record in self._records]
