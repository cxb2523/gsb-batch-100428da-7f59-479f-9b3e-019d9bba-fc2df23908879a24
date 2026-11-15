"""Image map (<map>) present.

# TODO(schema): requires <map>/<area> tag tracking.
Image maps are legacy and not tracked in the current schema.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ImageMapPresent(Rule):
    id = "image_map_present"
    name = "Image Map Present"
    category = "Mobile"
    severity = "info"
    description = "Página usa <map> / <area> (image maps), patrón problemático en móvil."
    fix_guidance = (
        "Reemplaza el image map por una solución CSS/SVG con áreas clicables "
        "individuales. Los <area> son difíciles de tocar con el dedo y poco "
        "accesibles."
    )
    references = [
        "https://web.dev/articles/responsive-web-design-basics",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs <map>/<area> tracking
        return iter([])
