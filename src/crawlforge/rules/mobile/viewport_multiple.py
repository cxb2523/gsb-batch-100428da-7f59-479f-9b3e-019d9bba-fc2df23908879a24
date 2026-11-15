"""Multiple viewport meta tags.

# TODO(schema): requires per-tag occurrence count for <meta name='viewport'>.
The schema's `urls.viewport` column stores only the value of the (first/last)
viewport tag found. To detect duplicates we'd need either a count column or
a list of all viewport values seen.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ViewportMultiple(Rule):
    id = "viewport_multiple"
    name = "Viewport Multiple"
    category = "Mobile"
    severity = "warning"
    description = "Múltiples <meta name='viewport'> en la misma página."
    fix_guidance = (
        "Deja un único <meta name='viewport'>. Tags duplicados pueden generar "
        "comportamientos imprevisibles entre navegadores."
    )
    references = [
        "https://web.dev/articles/responsive-web-design-basics",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs viewport tag count column
        return iter([])
