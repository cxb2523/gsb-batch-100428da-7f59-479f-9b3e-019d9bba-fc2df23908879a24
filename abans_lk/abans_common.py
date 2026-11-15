"""Shared fault-tolerant parsing helpers for the two Abans spiders.

The mobile-phone and laptop crawlers target the same buyabans.com page
structure, so every field-extraction and pagination decision lives here
once and is reused by both spiders.

Design rule: none of these helpers raise because a field or pagination
element is missing.  A value that cannot be found comes back as ``None``
(an empty value in the item), and a pagination cap that cannot be parsed
degrades to a bounded crawl instead of aborting the batch.
"""

import re

# Hard stop for listing pagination when the page-count element is missing
# or its text cannot be turned into an integer.  In that mode the crawler
# simply keeps advancing page numbers until a page yields no detail links
# or this cap is reached, whichever comes first.
FALLBACK_MAX_PAGE = 100

# Matches the first fragment that actually contains a digit, so labels and
# blank lines surrounding a price are ignored.
_HAS_DIGITS_RE = re.compile(r"\d")


def extract_text(selector):
    """Return stripped text for a ``::text``/``::attr(...)`` selector.

    Returns ``None`` when the selector matches nothing or only whitespace,
    instead of raising on a missing element.
    """
    if selector is None:
        return None
    try:
        raw = selector.get()
    except Exception:
        return None
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def extract_price(selector):
    """Extract a price from text nodes that may contain newlines/labels.

    Product price and old-price nodes on the site are rendered as
    multi-line raw text such as ``"\\nRs. 29,990\\n"`` (sometimes spread
    over several text nodes).  Returning the raw blob would store the
    whole paragraph as the price, and indexing a fixed node crashes when
    the markup changes.  Instead every text fragment is scanned, skipping
    empty/label lines, and the first fragment containing digits is
    returned stripped.  Returns ``None`` when nothing price-like exists.
    """
    if selector is None:
        return None
    try:
        nodes = selector.getall()
    except Exception:
        return None
    for raw in nodes or []:
        if not raw:
            continue
        for fragment in raw.splitlines():
            fragment = fragment.strip()
            if fragment and _HAS_DIGITS_RE.search(fragment):
                return fragment
    return None


def parse_max_page(text):
    """Parse the listing's last page number from its raw text.

    Returns a positive ``int`` on success.  When the element is missing
    (``None``), blank, or its text cannot be converted to an integer,
    returns ``None`` so the caller can fall back to bounded crawling
    rather than raising (e.g. the old ``int(None)`` crash).
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def should_continue_pagination(current_page, max_page, detail_links,
                               fallback_cap=FALLBACK_MAX_PAGE):
    """Decide whether the next listing page should be requested.

    * A page with no detail links always ends the crawl (natural end of
      the catalogue).
    * When ``max_page`` was parsed, the original rule is preserved: keep
      going while ``current_page <= max_page``.
    * When ``max_page`` is ``None`` (element missing / unparseable), keep
      going while ``current_page <= fallback_cap``.
    """
    if not detail_links:
        return False
    limit = max_page if isinstance(max_page, int) else fallback_cap
    return current_page <= limit
