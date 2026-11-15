"""Content not sized correctly to viewport.

# TODO(rendered): requires rendered DOM measurement.
Detection compares element widths against the viewport width post-render.
Schema lacks rendered layout dimensions.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ContentNotSizedCorrectly(Rule):
    id = "content_not_sized_correctly"
    name = "Content Not Sized Correctly To Viewport"
    category = "Mobile"
    severity = "warning"
    description = "Algún elemento es más ancho que el viewport (scroll horizontal)."
    fix_guidance = (
        "Localiza elementos con anchura fija mayor que la del viewport y "
        "convierte a porcentajes / max-width:100%. Evita scroll horizontal en "
        "móvil."
    )
    references = [
        "https://web.dev/viewport/",
    ]
    enabled_by_default = False
    requires_rendered_html = True

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs rendered layout widths
        return iter([])
