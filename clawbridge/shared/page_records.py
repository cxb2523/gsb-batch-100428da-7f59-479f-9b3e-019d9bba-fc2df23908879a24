"""Per-page capture records built from browser-use agent history.

browser-use keeps one ``AgentHistory`` item per agent step. Each item carries
the browser state *after* that step (``state.url`` / ``state.title``) plus the
``ActionResult`` list produced by that step. This module turns that history
into one verifiable record per unique page: URL, title, extracted snippet, and
whether content was really obtained for it.

Every function here is stdlib-only and duck-typed on the history object, so it
can be exercised in unit tests with a fake history and no real browser.

Record rules
------------
1. One record per unique page URL, ordered by first appearance.
2. ``captured`` is True only when content was actually extracted for that page
   (or, for the last page visited, returned in the agent's non-empty final
   result). Agent narration such as ``long_term_memory`` or error strings is
   never treated as page content.
3. A page without content is recorded as not captured, with an explicit reason.
   When the final result is empty the reason says so.
4. Repeated hits on an already-recorded URL add no record; they increment
   ``duplicate_skips`` on the original. Content found on a later visit is
   merged into that page's record (it is the same page).
5. When the history has no usable URL, a single not-captured record is returned
   instead of inventing a successful page.
"""

from __future__ import annotations

from typing import Any

# Maximum characters kept from a page's extracted content in a record.
PAGE_SNIPPET_MAX_CHARS = 500

_REASON_EMPTY_FINAL = "Final result was empty and no content was extracted for this page."
_REASON_NO_CONTENT = "No extracted content was returned for this page."
_REASON_NO_URL = "No usable URL in agent history."
_REASON_NO_URL_WITH_CONTENT = (
    "No usable URL in agent history; extracted content could not be attributed to any page."
)


def value_or_call(value: Any) -> Any:
    """Return ``value()`` when callable, else ``value``.

    browser-use exposes some history accessors as methods (``urls()``,
    ``action_results()``) and others as attributes, so callers cannot assume.
    Exceptions from a callable accessor yield ``None`` rather than propagating.
    """
    if callable(value):
        try:
            return value()
        except Exception:
            return None
    return value


def history_items(history: Any) -> list[Any]:
    """Return the ordered per-step items from an agent history object."""
    for attr in ("history", "items"):
        items = value_or_call(getattr(history, attr, None))
        if items is not None:
            try:
                return list(items)
            except TypeError:
                return []
    if isinstance(history, (list, tuple)):
        return list(history)
    return []


def final_result_text(history: Any, final_text: str | None = None) -> str:
    """Return the agent's genuine final answer as a stripped string ("" if empty)."""
    if final_text is None:
        final_text = value_or_call(getattr(history, "final_result", None))
    if final_text is None:
        return ""
    text = str(final_text).strip()
    if text in ("", "None", "null"):
        return ""
    return text


def item_url_and_title(item: Any) -> tuple[str, str]:
    """Extract the ``(url, title)`` pair recorded on a history item.

    browser-use nests these under ``item.state``; a plain ``item.url`` /
    ``item.title`` is accepted too so hand-built histories also work.
    """
    state = getattr(item, "state", None)
    url = getattr(state, "url", None) if state is not None else None
    title = getattr(state, "title", None) if state is not None else None
    if url is None:
        url = getattr(item, "url", None)
    if title is None:
        title = getattr(item, "title", None)
    url = url or ""
    title = title or ""
    if not isinstance(url, str):
        url = str(url)
    if not isinstance(title, str):
        title = str(title)
    return url.strip(), title.strip()


def url_is_usable(url: str) -> bool:
    """Whether a URL can have an extraction attributed to it.

    Blank URLs and browser-internal pseudo-pages (``about:blank``, ``data:``)
    carry no page identity, so they are treated as unavailable.
    """
    if not url:
        return False
    lowered = url.lower()
    return not (lowered.startswith("about:") or lowered.startswith("data:"))


def item_extracted_content(item: Any) -> str:
    """Return this step's extracted page content, if any.

    Only ``extracted_content`` counts. ``long_term_memory`` and error strings
    describe what the agent did, not what the page contained, so they must not
    be reported as an extraction. A step's results may also be exposed directly
    on the item (``item.extracted_content``) for hand-built histories.
    """
    results = getattr(item, "result", None)
    if results is None:
        results = getattr(item, "results", None)
    if results is None:
        direct = getattr(item, "extracted_content", None)
        return direct.strip() if isinstance(direct, str) and direct.strip() else ""
    if not isinstance(results, (list, tuple)):
        results = [results]
    for result in reversed(results):
        content = getattr(result, "extracted_content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def snippet(text: str) -> str:
    """Trim extracted text to the fixed snippet budget."""
    return (text or "").strip()[:PAGE_SNIPPET_MAX_CHARS]


def _no_url_record(items: list[Any], content: str) -> dict[str, Any]:
    """Record used when no page URL could be established (never a fake success)."""
    title = ""
    for item in reversed(items):
        _, item_title = item_url_and_title(item)
        if item_title:
            title = item_title
            break
    return {
        "url": "",
        "title": title,
        "snippet": snippet(content),
        "captured": False,
        "reason": _REASON_NO_URL_WITH_CONTENT if content else _REASON_NO_URL,
        "step": 1 if items else 0,
        "duplicate_skips": 0,
    }


def build_page_records(history: Any, final_text: str | None = None) -> list[dict[str, Any]]:
    """Build ordered, de-duplicated per-page capture records from agent history.

    Args:
        history: Agent history object (duck-typed; see module docstring).
        final_text: The agent's genuine final answer when the caller already
            extracted it. When omitted it is derived from
            ``history.final_result``. Callers must pass the *real* final result
            rather than a synthesized summary, otherwise a page could be marked
            captured without any content.

    Returns:
        A list of record dicts matching ``PageRecord`` fields, in first-visit
        order. Always non-empty: a history with no usable URL yields a single
        not-captured record explaining why.
    """
    items = history_items(history)
    final_text = final_result_text(history, final_text)

    records: list[dict[str, Any]] = []
    index_by_url: dict[str, int] = {}
    unattributed = ""
    last_index: int | None = None

    for step_no, item in enumerate(items, start=1):
        url, title = item_url_and_title(item)
        content = item_extracted_content(item)

        if not url_is_usable(url):
            if content and not unattributed:
                unattributed = content
            continue

        existing = index_by_url.get(url)
        if existing is not None:
            # Same page revisited: never add a record, just count the skip.
            record = records[existing]
            record["duplicate_skips"] += 1
            if not record["captured"] and content:
                # Revisit finally yielded the page's content -- same page, same record.
                record["snippet"] = snippet(content)
                record["captured"] = True
                record["reason"] = ""
            if not record["title"] and title:
                record["title"] = title
            last_index = existing
            continue

        captured = bool(content)
        if captured:
            reason = ""
        elif final_text:
            reason = _REASON_NO_CONTENT
        else:
            reason = _REASON_EMPTY_FINAL

        index_by_url[url] = len(records)
        last_index = len(records)
        records.append(
            {
                "url": url,
                "title": title,
                "snippet": snippet(content),
                "captured": captured,
                "reason": reason,
                "step": step_no,
                "duplicate_skips": 0,
            }
        )

    # The final answer is the agent's last extraction; attribute it to the last
    # page visited only when that page has no per-step content of its own.
    if final_text and last_index is not None and not records[last_index]["captured"]:
        records[last_index]["snippet"] = snippet(final_text)
        records[last_index]["captured"] = True
        records[last_index]["reason"] = ""

    if records:
        return records
    return [_no_url_record(items, unattributed or final_text)]
