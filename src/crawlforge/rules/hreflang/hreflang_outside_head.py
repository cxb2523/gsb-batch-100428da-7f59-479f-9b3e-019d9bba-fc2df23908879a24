"""Hreflang outside <head> — placeholder.

# TODO(schema): el schema actual no rastrea la posición DOM de las
# anotaciones hreflang (solo from_html / from_header / from_sitemap). Para
# detectar declaraciones fuera de <head> se necesitaría una columna como
# `hreflang.in_head BOOLEAN` o similar.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HreflangOutsideHead(Rule):
    id = "hreflang_outside_head"
    name = "Hreflang Outside <head>"
    category = "Hreflang"
    severity = "warning"
    description = (
        "Una anotación hreflang aparece fuera del <head> del documento HTML."
    )
    fix_guidance = (
        "Mueve los <link rel='alternate' hreflang='...'> al <head> del HTML."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/international/localized-versions",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query: the schema does not yet record DOM position
        return iter([])
