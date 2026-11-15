"""Noindex Only In Source (HTML rendered diff).

# TODO(schema): requires diff between raw HTML meta robots and rendered
HTML meta robots. Crawler does not currently parse meta_robots
separately for raw vs rendered.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class NoindexOnlyInSource(Rule):
    id = "noindex_only_in_source"
    name = "Noindex Only In Source"
    category = "Directives"
    severity = "warning"
    description = (
        "El `noindex` aparece en el HTML inicial (source) pero JavaScript lo "
        "elimina al renderizar. Google indexará la página renderizada."
    )
    fix_guidance = (
        "Decide si la página debe indexarse o no, y aplica la directiva de "
        "forma consistente en source y rendered."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag",
    ]
    enabled_by_default = False
    requires_rendered_html = True

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — schema does not yet split source/rendered meta
        return iter([])
