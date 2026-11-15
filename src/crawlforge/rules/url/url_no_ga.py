"""No GA code.

# TODO(schema): requires extracting Google Analytics IDs from the page.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlNoGa(Rule):
    id = "url_no_ga"
    name = "No GA code"
    category = "URL"
    severity = "info"
    description = "Página sin Google Analytics."
    fix_guidance = (
        "Si tu sitio depende de Google Analytics, asegúrate de que el "
        "tracking code esté presente en todas las páginas indexables."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]
    enabled_by_default = False  # awaiting GA tracking-id extraction

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder — needs GA tracking-id extraction
        return iter([])
