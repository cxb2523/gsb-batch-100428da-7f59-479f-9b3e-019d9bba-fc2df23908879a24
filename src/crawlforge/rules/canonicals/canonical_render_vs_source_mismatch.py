"""TODO(schema): requires source vs rendered canonical columns."""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class CanonicalRenderVsSourceMismatch(Rule):
    id = "canonical_render_vs_source_mismatch"
    name = "Canonical Render vs Source Mismatch"
    category = "Canonicals"
    severity = "warning"
    description = "El canonical cambia entre el HTML servido y el HTML renderizado."
    fix_guidance = (
        "El canonical no debería cambiar al renderizar JS. Si lo hace, "
        "decide cuál es la versión correcta y servirla siempre desde el "
        "HTML inicial para evitar señales contradictorias."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    ]
    # TODO(schema): needs urls.canonical_source + urls.canonical_rendered columns.
    requires_rendered_html = True
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        return iter([])
