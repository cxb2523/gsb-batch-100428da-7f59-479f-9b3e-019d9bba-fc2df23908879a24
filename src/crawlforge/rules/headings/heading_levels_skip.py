"""Heading levels skipped (e.g. h1 -> h3 without h2).

# TODO(schema): the schema stores h1..h6 as independent text arrays without
# DOM order or document position, so we cannot reliably detect skipped
# levels. Re-enable when an extractor adds an ordered `headings` table with
# (level, position, text).
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HeadingLevelsSkip(Rule):
    id = "heading_levels_skip"
    name = "Heading Levels Skipped"
    category = "Headings"
    severity = "info"
    description = (
        "Saltos en la jerarquía de encabezados (p.ej. h1 → h3 sin h2) "
        "(no detectable con el schema actual)."
    )
    fix_guidance = (
        "Mantén la jerarquía secuencial: usa h1 → h2 → h3 sin saltar niveles "
        "para mejorar accesibilidad y comprensión semántica."
    )
    references = [
        "https://www.w3.org/WAI/tutorials/page-structure/headings/",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — heading hierarchy not tracked
        return iter([])
