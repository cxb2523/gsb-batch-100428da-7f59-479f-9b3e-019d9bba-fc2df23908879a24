"""Unsupported plugins (Flash, Silverlight, applet, embed/object).

# TODO(schema): requires extraction of <embed>, <object>, <applet> tags.
Schema does not currently track plugin elements.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UnsupportedPlugins(Rule):
    id = "unsupported_plugins"
    name = "Unsupported Plugins"
    category = "Mobile"
    severity = "warning"
    description = "Página usa plugins no soportados en móvil (<embed>, <object>, <applet>)."
    fix_guidance = (
        "Sustituye Flash/Silverlight/Java applets por HTML5/CSS/JS. Los móviles "
        "modernos no ejecutan estos plugins y la página será no mobile-friendly."
    )
    references = [
        "https://web.dev/articles/responsive-web-design-basics",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs <embed>/<object>/<applet> tracking
        return iter([])
