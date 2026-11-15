from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HreflangMissingAnnotations(Rule):
    id = "hreflang_missing_annotations"
    name = "Hreflang Missing Annotations"
    category = "Hreflang"
    severity = "warning"
    enabled_by_default = False  # TODO(schema): heuristic detection of multilingual sites
    description = (
        "Páginas que parecen tener equivalentes en otros idiomas pero no "
        "declaran hreflang. Detección heurística pendiente."
    )
    fix_guidance = (
        "Si tu sitio tiene versiones en múltiples idiomas, declara hreflang "
        "explícito en cada URL del cluster (HTML, sitemap o header)."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/international/localized-versions",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # TODO(schema): requires multilingual cluster heuristic across URLs
        return iter([])
