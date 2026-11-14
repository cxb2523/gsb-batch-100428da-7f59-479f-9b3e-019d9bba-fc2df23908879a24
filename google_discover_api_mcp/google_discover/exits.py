"""One read of the feed for collect_discover_cards, through a new connection.

This file is the repo's own. The hosted service takes a new home IP in the country for each read; here
every read leaves from your connection, or from your proxy, which may give each connection another IP.
"""

from __future__ import annotations

from typing import Any, Protocol

from google_discover_api_mcp.google_discover.client import ask


class BoundLogger(Protocol):
    def info(self, event: str, **fields: Any) -> Any: ...
    def warning(self, event: str, **fields: Any) -> Any: ...


class ExitContext(Protocol):
    """What a multi-read collection needs: a fresh connection per read and a log.

    It deliberately exposes neither ``cache_get`` nor ``cache_set``. The 15-minute cache of one country
    and language belongs to the single-read tools (``get_discover_feed``, ``check_domain_in_feed``); a
    collection is many independent reads from new exits, so it must neither write to nor read that
    snapshot, or callers could mistake a stale single read for a fresh collection.
    """

    def new_http(self) -> Any: ...

    @property
    def log(self) -> BoundLogger: ...


async def ask_from_new_exit(ctx: ExitContext, language: str) -> bytes:
    """The raw answer of one read through a new connection."""
    return await ask(ctx.new_http(), ctx.log, language)
