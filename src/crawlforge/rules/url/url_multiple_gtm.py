"""Multiple GTM codes.

# TODO(schema): requires extracting GTM container IDs from the page (script
# tags or noscript fragments). No column or table exposes this today.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlMultipleGtm(Rule):
    id = "url_multiple_gtm"
    name = "Multiple GTM codes"
    category = "URL"
    severity = "warning"
    description = "Página con dos o más contenedores Google Tag Manager (GTM)."
    fix_guidance = (
        "Consolida en un único contenedor GTM por página. Múltiples "
        "contenedores generan duplicación de eventos y problemas de medición."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]
    enabled_by_default = False  # awaiting GTM container extraction

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder — needs GTM container ID extraction
        return iter([])
