"""Helpers for deciding whether a search crawler may request another page.

Search APIs are not always well behaved: pagination markers can disappear,
change type, repeat themselves or announce the end of the result set.  The
helpers in this module never raise for those cases.  Instead they return a
:class:`PaginationDecision` carrying the reason pagination stopped, so a
spider can log a checkable stop reason and end the crawl safely.

Two cursor styles are supported:

``CURSOR_NUMERIC``
    The response contains a numeric cursor that is used as the next page
    number (kuaishou's ``pcursor``).

``CURSOR_HAS_MORE``
    The response contains a ``has_more``-style flag (``0``/``1`` or
    ``False``/``True``) and the spider advances by incrementing its own page
    counter (quanmin, haokan and ixigua).
"""

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

# Reasons a page walk stopped.  These strings are logged verbatim.
CONTINUE = "continue"
NO_MORE = "no_more"
INVALID_CURSOR = "invalid_cursor"
REPEATED_CURSOR = "repeated_cursor"
COUNT_REACHED = "count_reached"
EMPTY_PAGE = "empty_page"

CURSOR_NUMERIC = "numeric"
CURSOR_HAS_MORE = "has_more"

# String values that explicitly mean "there is no next page".  JSON ``null``
# and empty/whitespace strings are handled structurally by the helpers.
TERMINAL_CURSORS = ("no_more", "null")


class _Missing:
    """Sentinel for a pagination field that is absent from the response."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"


MISSING = _Missing()


@dataclass(frozen=True)
class PaginationDecision:
    """Result of evaluating whether another search page may be fetched."""

    reason: str
    next_page: Optional[int] = None

    @property
    def should_continue(self) -> bool:
        return self.reason == CONTINUE


def decide_next_page(
    raw_cursor: Any,
    *,
    current_page: int,
    entries_found: bool,
    previous_cursor: Any = None,
    count_allows_next: Optional[Callable[[int], bool]] = None,
    cursor_kind: str = CURSOR_NUMERIC,
    terminal_values: Iterable[Any] = TERMINAL_CURSORS,
) -> PaginationDecision:
    """Decide whether the page that was just parsed should be followed.

    ``raw_cursor`` is the pagination marker exactly as returned by the site
    (``None`` for a JSON null, :data:`MISSING` when the field is absent).
    ``current_page`` is the page number that produced this response.
    ``previous_cursor`` is the cursor value that was used to request the current
    page; it is only meaningful for numeric cursors.  ``entries_found`` says
    whether at least one entry on the page could be parsed.
    ``count_allows_next`` receives the candidate next page number and returns
    whether the configured count limit still permits another request; each
    spider supplies its original count expression there.

    Terminal markers and malformed markers stop the walk before the empty-page
    and count checks.  A missing marker is treated as malformed rather than as
    an announced end, because a response that lost the field cannot tell us
    whether more pages exist.  When the decision is ``CONTINUE``,
    ``next_page`` is the page number to request.
    """

    terminal_strings = {
        value.strip().lower()
        for value in terminal_values
        if isinstance(value, str) and value.strip()
    }

    if _is_terminal(raw_cursor, terminal_strings, cursor_kind):
        return PaginationDecision(NO_MORE)

    cursor_value = _parse_cursor(raw_cursor, cursor_kind)
    if cursor_value is None:
        return PaginationDecision(INVALID_CURSOR)

    page_number = _coerce_page(current_page)

    if cursor_kind == CURSOR_NUMERIC and previous_cursor is not None:
        previous_value = _parse_cursor(previous_cursor, cursor_kind)
        if previous_value is not None and cursor_value <= previous_value:
            # Same cursor as the page that was just fetched, or one that
            # moves backwards: requesting it again would not make progress.
            return PaginationDecision(REPEATED_CURSOR)

    if cursor_kind == CURSOR_NUMERIC:
        candidate_page = cursor_value
    else:
        candidate_page = page_number + 1

    if not entries_found:
        # The marker looked usable, but this page produced nothing parsable.
        # Asking for the next page cannot rescue the entries that were lost,
        # so stop rather than walk an unbounded sequence of empty pages.
        return PaginationDecision(EMPTY_PAGE)

    if count_allows_next is not None:
        try:
            allowed = bool(count_allows_next(candidate_page))
        except Exception:
            # A broken count configuration must not abort the crawl.
            allowed = False
        if not allowed:
            return PaginationDecision(COUNT_REACHED)

    return PaginationDecision(CONTINUE, next_page=candidate_page)


def log_pagination_stop(logger, site: str, reason: str, page: int) -> None:
    """Log why a spider stopped walking search result pages."""

    logger.info(
        "search pagination stopped: site=%s reason=%s page=%s",
        site,
        reason,
        page,
    )


def _is_terminal(raw_cursor: Any, terminal_strings: set, cursor_kind: str) -> bool:
    if raw_cursor is MISSING:
        # A missing marker is a malformed response, not an announced end.
        return False
    if raw_cursor is None:
        return True
    if isinstance(raw_cursor, str):
        text = raw_cursor.strip()
        return not text or text.lower() in terminal_strings
    if cursor_kind == CURSOR_HAS_MORE:
        if isinstance(raw_cursor, bool):
            return not raw_cursor
        if isinstance(raw_cursor, int):
            return raw_cursor == 0
    return False


def _parse_cursor(raw_cursor: Any, cursor_kind: str) -> Optional[int]:
    if cursor_kind == CURSOR_HAS_MORE:
        # JSON ``has_more`` flags are numeric or boolean; strings such as
        # ``"1"`` are a type mismatch and must not drive pagination.
        if isinstance(raw_cursor, bool):
            return int(raw_cursor)
        if isinstance(raw_cursor, int):
            return raw_cursor
        return None

    if isinstance(raw_cursor, bool):
        return None
    if isinstance(raw_cursor, int):
        value = raw_cursor
    elif isinstance(raw_cursor, str):
        text = raw_cursor.strip()
        if not text.isascii() or not text.isdigit():
            return None
        value = int(text)
    else:
        return None
    return value if value >= 0 else None


def _coerce_page(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
