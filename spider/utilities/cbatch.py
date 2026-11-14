# _*_ coding: utf-8 _*_

"""
cbatch.py by xianhu

Utilities describing the final state of one thread pool run: why urls failed,
a bounded detail recorder, and the dict-like result returned to callers.
"""

import collections
import threading


class FailureStage(object):
    """
    stages which can produce a failure
    """
    FETCH = "fetch"
    PARSE = "parse"
    SAVE = "save"


class FailureReason(object):
    """
    reasons why a url is recorded as a failure
    """
    FETCH_FAILED = "fetch_failed"  # fetcher returned state_code < 0
    RETRY = "retry"  # fetcher asked for repeat, the task was put back to the queue
    RETRY_EXHAUSTED = "retry_exhausted"  # max_repeat was reached, the task was not put back
    PROXY_INVALID = "proxy_invalid"  # the proxies used by this attempt were unavailable (state_proxies=-1)
    PROXY_RETURNED = "proxy_returned"  # the proxies used by this attempt were returned to the queue (state_proxies=0)
    PARSE_FAILED = "parse_failed"  # parser returned state_code <= 0
    SAVE_FAILED = "save_failed"  # saver returned state_code <= 0


class BatchOutcome(object):
    """
    possible outcomes of one thread pool run
    """
    COMPLETED = "completed"  # all work drained, no failure at all
    COMPLETED_WITH_FAILURES = "completed_with_failures"  # all work drained, but some tasks failed
    RETRY_EXHAUSTED = "retry_exhausted"  # at least one url used up max_repeat
    PROXIES_EXHAUSTED = "proxies_exhausted"  # at least one url failed because proxies were unavailable
    RETRY_AND_PROXIES_EXHAUSTED = "retry_and_proxies_exhausted"  # both of the above
    INCOMPLETE = "incomplete"  # queued work was still pending when the pool stopped


class FailureRecorder(object):
    """
    class of FailureRecorder, record failure details with a bounded length

    every failure event increments the counters, but every detail collection
    keeps at most max_failures distinct (stage, url) pairs, so a long running
    spider does not grow its memory without bound.
    """

    def __init__(self, max_failures=100):
        """
        constructor
        :param max_failures: maximum retained failure details, 0 to retain none
        """
        try:
            max_failures = int(max_failures)
        except (TypeError, ValueError):
            max_failures = 100
        self._max_failures = max(0, max_failures)
        self._items = collections.OrderedDict()  # (stage, url) -> terminal failure detail
        self._pending = collections.OrderedDict()  # (stage, url) -> retry detail, will be retried
        self._reasons = collections.Counter()  # reason -> terminal event count
        self._pending_reasons = collections.Counter()  # reason -> retry event count
        self._total = 0
        self._terminal_total = 0
        self._lock = threading.Lock()
        return

    @property
    def max_failures(self):
        """
        maximum retained failure details
        """
        return self._max_failures

    @staticmethod
    def _merge_reasons(old, new):
        """
        merge two reason lists, keeping their order and dropping duplicates
        """
        merged = list(old or [])
        for reason in new or []:
            if reason and reason not in merged:
                merged.append(reason)
        return merged

    def record(self, url, stage, reasons, terminal=True, **details):
        """
        record one failure event
        :param url: url of the failed task
        :param stage: one of FailureStage values
        :param reasons: list of FailureReason values
        :param terminal: True if the task finally failed, False if it will be retried
        :param details: extra fields such as repeat, state_code, excep_class, excep_string
        """
        reasons = [reason for reason in (reasons or []) if reason]
        url = url or ""
        stage = stage or ""
        key = (stage, url)

        with self._lock:
            self._total += 1
            if terminal:
                self._terminal_total += 1
                for reason in reasons:
                    self._reasons[reason] += 1
            else:
                for reason in reasons:
                    self._pending_reasons[reason] += 1

            if self._max_failures > 0:
                previous = self._pending.pop(key, None) or (self._items.get(key) if terminal else None)
                merged = self._merge_reasons(previous.get("reasons") if previous else None, reasons)
                entry = {
                    "url": url,
                    "stage": stage,
                    # reason is the reason why it finally failed, reasons keeps the whole history
                    "reason": (reasons or merged or [""])[0] if terminal else (merged or [""])[0],
                    "reasons": merged,
                    "terminal": bool(terminal),
                }
                for name, value in details.items():
                    if value is not None:
                        entry[name] = value

                if terminal:
                    self._pending.pop(key, None)
                    self._items.pop(key, None)
                    self._items[key] = entry
                    while len(self._items) > self._max_failures:
                        self._items.popitem(last=False)
                else:
                    self._pending.pop(key, None)
                    self._pending[key] = entry
                    while len(self._pending) > self._max_failures:
                        self._pending.popitem(last=False)
            else:
                entry = {"url": url, "stage": stage, "reason": reasons[0] if reasons else "",
                         "reasons": list(reasons), "terminal": bool(terminal)}
                for name, value in details.items():
                    if value is not None:
                        entry[name] = value
        return entry

    def resolve(self, url, stage):
        """
        drop the pending retry detail of a task, called when it finally succeeds
        """
        with self._lock:
            self._pending.pop((stage or "", url or ""), None)
        return

    @property
    def total(self):
        """
        total number of failure events, including the ones whose details were dropped
        """
        with self._lock:
            return self._total

    @property
    def terminal_total(self):
        """
        total number of terminal failure events
        """
        with self._lock:
            return self._terminal_total

    def reason_counts(self, terminal=True):
        """
        get counters keyed by reason, terminal=True for failures, False for retries, None for both
        """
        with self._lock:
            if terminal is True:
                counts = collections.Counter(self._reasons)
            elif terminal is False:
                counts = collections.Counter(self._pending_reasons)
            else:
                counts = collections.Counter(self._reasons)
                counts.update(self._pending_reasons)
            return dict(counts)

    def snapshot(self):
        """
        return (retained terminal failure details, retained retry details, total events, terminal events)
        """
        with self._lock:
            items = [dict(item, reasons=list(item["reasons"])) for item in self._items.values()]
            pending = [dict(item, reasons=list(item["reasons"])) for item in self._pending.values()]
            return items, pending, self._total, self._terminal_total


class BatchResult(dict):
    """
    dict-like result of one thread pool run

    it contains all original counters keyed by TPEnum, plus keys such as:
    outcome, converged, drained, success, failures, failed_urls,
    failure_reasons and failure/retry counts.
    counters can also be read by their string value (e.g. "url_fetch_succ"),
    and values are reachable as attributes (e.g. result.outcome).
    """

    def __init__(self, *args, aliases=None, **kwargs):
        """
        constructor, aliases maps a string key to the real key stored in this dict
        """
        super().__init__(*args, **kwargs)
        self._aliases = dict(aliases or {})
        return

    def __missing__(self, key):
        """
        resolve a string alias such as "url_fetch_succ" to its TPEnum key
        """
        aliases = self.__dict__.get("_aliases") or {}
        alias = aliases.get(key) if isinstance(key, str) else None
        if alias is not None:
            return dict.__getitem__(self, alias)
        raise KeyError(key)

    def __contains__(self, key):
        """
        support "url_fetch_succ" in result checks
        """
        if dict.__contains__(self, key):
            return True
        aliases = self.__dict__.get("_aliases") or {}
        return isinstance(key, str) and key in aliases

    def get(self, key, default=None):
        """
        get a value by TPEnum or by its string value
        """
        try:
            return self[key]
        except KeyError:
            return default

    def __getattr__(self, name):
        """
        allow result.outcome style access
        """
        # never recurse on private/dunder names: they are resolved by the dict itself,
        # and _aliases may not exist yet while copying or unpickling
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None
