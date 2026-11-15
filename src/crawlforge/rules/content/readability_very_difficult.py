"""Readability very difficult rule.

# TODO(schema): readability metrics not stored.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ReadabilityVeryDifficult(Rule):
    id = "readability_very_difficult"
    name = "Readability Very Difficult"
    category = "Content"
    severity = "info"
    description = "Páginas con legibilidad muy difícil (requiere puntuación tipo Flesch)."
    fix_guidance = "Reescribe el texto en un nivel de lectura más accesible."
    references = [
        "https://developers.google.com/search/docs/fundamentals/creating-helpful-content",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder: needs readability score column
        return iter([])
