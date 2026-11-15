"""Standalone entry point that refreshes the ``hotsearch`` table.

The implementation lives in :mod:`hotsearch` and is shared with ``main.py``.
Run this module directly to perform one collection run::

    python spider.py

The run prints one summary line and exits non-zero when nothing could be
stored.
"""

import hotsearch
from hotsearch import (
    HotSearchCollectionError,
    HotSearchResult,
    close_conn,
    get_conn,
)

__all__ = [
    "get_conn",
    "close_conn",
    "get_baidu_hot",
    "update_hotsearch",
    "collect_hotsearch",
    "store_hotsearch",
    "run_hotsearch",
    "HotSearchResult",
    "HotSearchCollectionError",
]


def get_baidu_hot():
    """Backwards-compatible wrapper returning ``[(title, heat), ...]``.

    Kept for callers of the old API.  New code should call
    :func:`hotsearch.collect_hotsearch` and inspect the structured result,
    which also exposes dropped entries and the failing stage.
    """
    result = hotsearch.collect_hotsearch()
    return [(item.title, item.heat) for item in result.items]


def collect_hotsearch(*args, **kwargs):
    """Delegate to :func:`hotsearch.collect_hotsearch`."""
    return hotsearch.collect_hotsearch(*args, **kwargs)


def store_hotsearch(*args, **kwargs):
    """Delegate to :func:`hotsearch.store_hotsearch`."""
    return hotsearch.store_hotsearch(*args, **kwargs)


def run_hotsearch(*args, **kwargs):
    """Delegate to :func:`hotsearch.run_hotsearch`."""
    return hotsearch.run_hotsearch(*args, **kwargs)


def update_hotsearch(*args, **kwargs):
    """Delegate to :func:`hotsearch.update_hotsearch`."""
    return hotsearch.update_hotsearch(*args, **kwargs)


if __name__ == "__main__":
    try:
        update_hotsearch()
    except HotSearchCollectionError as exc:
        # Non-zero exit so schedulers/CI can tell this run produced nothing.
        raise SystemExit(f"[hotsearch] {exc}") from None
