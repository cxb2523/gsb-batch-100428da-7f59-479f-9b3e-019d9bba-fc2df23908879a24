"""URLs In Multiple Sitemaps.

# TODO(schema): requires tracking N-to-N URL ↔ sitemap relationships
(currently `urls.from_sitemap` is just a boolean — we don't know which
sitemap or how many).
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlsInMultipleSitemaps(Rule):
    id = "urls_in_multiple_sitemaps"
    name = "URLs In Multiple Sitemaps"
    category = "Sitemaps"
    severity = "info"
    description = (
        "Una misma URL aparece en más de un sitemap.xml. Aunque el "
        "protocolo lo permite, suele ser señal de configuración inconsistente."
    )
    fix_guidance = (
        "Mantén cada URL en un único sitemap. Si usas sitemap-index, asegúrate "
        "de que los sitemaps hijos no se solapan."
    )
    references = [
        "https://www.sitemaps.org/protocol.html",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — schema only stores boolean from_sitemap
        return iter([])
