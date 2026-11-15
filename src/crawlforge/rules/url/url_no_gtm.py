"""No GTM code.

# TODO(schema): requires extracting GTM container IDs from the page.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlNoGtm(Rule):
    id = "url_no_gtm"
    name = "No GTM code"
    category = "URL"
    severity = "info"
    description = "Página sin contenedor Google Tag Manager."
    fix_guidance = (
        "Si tu sitio depende de GTM para tracking, asegúrate de que el "
        "snippet esté en el <head> de TODAS las páginas (incluido 404)."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]
    enabled_by_default = False  # awaiting GTM container extraction

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder — needs GTM container ID extraction
        return iter([])
