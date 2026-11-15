# -*- coding: utf-8 -*-
"""In-memory stand-in for a pymongo collection used by the test suite.

Only the small surface the pipeline relies on is implemented, plus the bits
needed to assert deduplication behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FakeUpdateResult:
    matched_count: int = 0
    modified_count: int = 0
    upserted_id: Any = None


class FakeCollection:
    """A dict-like collection keyed by ``url``.

    Parameters
    ----------
    fail_on_urls:
        URLs for which every write raises, to exercise per item isolation.
    fail_on_call:
        1-based write call indexes that raise, for order based failure tests.
    """

    def __init__(self, fail_on_urls=None, fail_on_call=None):
        self.documents: list[dict[str, Any]] = []
        self.fail_on_urls = set(fail_on_urls or ())
        self.fail_on_call = set(fail_on_call or ())
        self.write_calls = 0
        self.read_calls = 0

    # -- write API ---------------------------------------------------------
    def replace_one(self, filter, replacement, upsert=False):
        return self._write("replace_one", filter, replacement, upsert)

    def _write(self, kind, filter, replacement, upsert):
        self.write_calls += 1
        if self.write_calls in self.fail_on_call:
            raise RuntimeError("injected write failure on call %d" % self.write_calls)
        url = (filter or {}).get("url")
        if url in self.fail_on_urls:
            raise RuntimeError("injected write failure for url %r" % url)

        for index, document in enumerate(self.documents):
            if document.get("url") == url:
                self.documents[index] = dict(replacement)
                return FakeUpdateResult(matched_count=1, modified_count=1)
        if not upsert:
            return FakeUpdateResult(matched_count=0, modified_count=0)
        self.documents.append(dict(replacement))
        return FakeUpdateResult(
            matched_count=0, modified_count=0, upserted_id=len(self.documents)
        )

    # -- read API ----------------------------------------------------------
    def find_one(self, filter=None):
        self.read_calls += 1
        url = (filter or {}).get("url")
        for document in self.documents:
            if document.get("url") == url:
                return dict(document)
        return None

    def count_documents(self, filter=None):
        self.read_calls += 1
        if not filter:
            return len(self.documents)
        url = filter.get("url")
        return sum(1 for d in self.documents if d.get("url") == url)

    @property
    def urls(self):
        return [d.get("url") for d in self.documents]


class ExplodingCollection:
    """Every write raises; useful to prove the pipeline degrades gracefully."""

    def __init__(self):
        self.write_calls = 0

    def replace_one(self, filter, replacement, upsert=False):
        self.write_calls += 1
        raise RuntimeError("collection unavailable")

    def update_one(self, filter, update, upsert=False):
        self.write_calls += 1
        raise RuntimeError("collection unavailable")
