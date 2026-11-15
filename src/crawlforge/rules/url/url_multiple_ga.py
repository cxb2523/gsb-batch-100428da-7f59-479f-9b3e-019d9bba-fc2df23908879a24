"""Multiple GA codes.

# TODO(schema): requires extracting Google Analytics IDs (UA-/G-/MO-) from
# the page. No column exposes this today.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlMultipleGa(Rule):
    id = "url_multiple_ga"
    name = "Multiple GA codes"
    category = "URL"
    severity = "warning"
    description = "Página con dos o más IDs de Google Analytics."
    fix_guidance = (
        "Mantén una única instalación de Google Analytics por página para "
        "evitar duplicación de hits y métricas inconsistentes."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]
    enabled_by_default = False  # awaiting GA tracking-id extraction

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder — needs GA tracking-id extraction
        return iter([])
