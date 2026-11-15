"""Font too small for mobile.

# TODO(rendered): requires computed CSS font-size per text element.
Schema does not store rendered font sizes.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class FontTooSmallMobile(Rule):
    id = "font_too_small_mobile"
    name = "Font Too Small For Mobile"
    category = "Mobile"
    severity = "warning"
    description = "Texto con tamaño de fuente inferior a 12 px en móvil."
    fix_guidance = (
        "Aumenta el font-size base a ≥16px y nunca uses <12px en móvil. "
        "Google Mobile-Friendly Test marca esto explícitamente."
    )
    references = [
        "https://web.dev/font-size/",
    ]
    enabled_by_default = False
    requires_rendered_html = True

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs computed font-size per text node
        return iter([])
