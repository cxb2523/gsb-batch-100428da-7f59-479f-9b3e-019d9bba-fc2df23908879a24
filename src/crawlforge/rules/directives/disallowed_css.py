"""Disallowed CSS.

# TODO(schema): requires cross-referencing CSS resources against
robots.txt rules.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class DisallowedCss(Rule):
    id = "disallowed_css"
    name = "Disallowed CSS"
    category = "Directives"
    severity = "warning"
    description = (
        "Un archivo CSS referenciado en la página está bloqueado por "
        "robots.txt, lo que puede impedir que Google entienda el layout y "
        "evalúe correctamente mobile-friendliness."
    )
    fix_guidance = (
        "Permite el rastreo de los archivos CSS principales en robots.txt."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — resources don't track from_robots yet
        return iter([])
