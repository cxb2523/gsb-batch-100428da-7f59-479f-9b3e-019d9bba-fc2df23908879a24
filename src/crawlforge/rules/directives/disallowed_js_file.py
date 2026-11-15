"""Disallowed JS File.

# TODO(schema): requires cross-referencing referenced JS resources against
robots.txt rules. We do not currently mark resources as `from_robots`.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class DisallowedJsFile(Rule):
    id = "disallowed_js_file"
    name = "Disallowed JS File"
    category = "Directives"
    severity = "warning"
    description = (
        "Un archivo JavaScript referenciado por la página está bloqueado "
        "por robots.txt, lo que puede impedir el correcto renderizado por "
        "parte de Google."
    )
    fix_guidance = (
        "Permite el rastreo de los archivos JS críticos para el render. "
        "Google necesita ejecutar JS para entender la página completa."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — resources don't track from_robots yet
        return iter([])
