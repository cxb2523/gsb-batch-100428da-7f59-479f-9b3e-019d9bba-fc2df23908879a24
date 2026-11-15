"""Disallowed Image.

# TODO(schema): requires cross-referencing image resources against
robots.txt rules.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class DisallowedImage(Rule):
    id = "disallowed_image"
    name = "Disallowed Image"
    category = "Directives"
    severity = "warning"
    description = (
        "Una imagen referenciada en la página está bloqueada por robots.txt, "
        "lo que la excluye de Google Images."
    )
    fix_guidance = (
        "Permite el rastreo de imágenes que quieras ver en Google Images. "
        "Solo bloquea cuando las imágenes sean privadas/sensibles."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — resources don't track from_robots yet
        return iter([])
