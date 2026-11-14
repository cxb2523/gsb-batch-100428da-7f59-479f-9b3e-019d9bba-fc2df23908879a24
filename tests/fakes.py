"""Fakes for the collect_discover_cards tests: a scripted HTTP client, a fresh exit per read, a tiny context.

No network, no Chrome keys, no curl_cffi: each fake HTTP answer is a hand-built feedwire-shaped protobuf
that google_discover.protowire can read back, and google_discover.client.cards turns into cards.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Make the package importable when the tests are run with `python -I -m pytest` (no .pth files).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google_discover_api_mcp.core.errors import ErrorCode, ToolError
from google_discover_api_mcp.google_discover import protowire as pw


def wire_string(text: str) -> bytes:
    """One length-delimited protobuf field carrying a string; cards() only needs the ordered string list."""
    return pw.message(100, text.encode())


def feedwire(card_specs: list[dict[str, str]]) -> bytes:
    """A raw answer whose strings, in order, are the five card fields and an age, repeated per card."""
    body = b""
    for spec in card_specs:
        body += b"".join(wire_string(spec[key]) for key in ("url", "title", "image", "publisher", "favicon"))
        body += wire_string(spec["age"])
    return pw.message(1, body)


def make_card(
    url: str, *, title: str | None = None, publisher: str = "Example News", age: str = "3h"
) -> dict[str, str]:
    return {
        "url": url,
        "title": title or f"Story of {url}",
        "image": "https://encrypted-tbn0.gstatic.com/images?q=tbn:example",
        "publisher": publisher,
        "favicon": "https://www.google.com/s2/favicons?domain=example.com",
        "age": age,
    }


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = b"") -> None:
        self.status = status
        self.content = content


class FakeHttp:
    """One HTTP client = one exit. Every request takes the next line of the shared script."""

    def __init__(self, script: list[Any], counter: list[int]) -> None:
        self._script = script
        self._counter = counter
        self.requests = 0

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        index = self._counter[0]
        self._counter[0] += 1
        self.requests += 1
        if index >= len(self._script):
            raise AssertionError(f"scripted only {len(self._script)} reads, read #{index + 1} was not scripted")
        line = self._script[index]
        if isinstance(line, BaseException):
            raise line
        if isinstance(line, int):  # an HTTP status to fail with
            return FakeResponse(status=line)
        if isinstance(line, dict):
            return FakeResponse(content=feedwire([line]))
        if line == b"":  # a well-formed answer with no cards
            return FakeResponse(content=b"")
        return FakeResponse(content=feedwire(list(line)))  # a list of card specs


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


class FakeContext:
    """Stands in for ToolContext. A fresh FakeHttp per new_http(), like a fresh exit per read."""

    def __init__(self, script: list[Any], cache: dict[str, Any] | None = None) -> None:
        self.profile = type("Profile", (), {"country": "US"})()
        self.log = FakeLog()
        self.cache = cache if cache is not None else {}
        self._script = script
        self._counter = [0]
        self.exits: list[FakeHttp] = []

    def new_http(self) -> FakeHttp:
        client = FakeHttp(self._script, self._counter)
        self.exits.append(client)
        return client

    # The cache surface exists on the real context; the collection wrapper must never reach it.
    async def cache_get(self, key: str) -> None:
        raise AssertionError("collect_discover_cards must not read the single-read cache")

    async def cache_set(self, key: str, value: str, ttl_s: int) -> None:
        raise AssertionError("collect_discover_cards must not write the single-read cache")


def rate_limited() -> ToolError:
    return ToolError("Google is rate limiting the feed. Try again in a minute.", ErrorCode.RATE_LIMITED)
