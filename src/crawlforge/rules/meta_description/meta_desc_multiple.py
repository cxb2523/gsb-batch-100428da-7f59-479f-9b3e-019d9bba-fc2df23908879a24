"""Meta description multiple.

# TODO(schema): the schema only stores a single `meta_description` VARCHAR per
# URL, so we cannot detect that a page declared more than one
# <meta name="description"> tag. Re-enable when an extractor counter or list
# column is added (e.g. `meta_description_count INTEGER`).
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class MetaDescMultiple(Rule):
    id = "meta_desc_multiple"
    name = "Meta Description Multiple"
    category = "Meta Description"
    severity = "warning"
    description = (
        "La página declara más de un <meta name=\"description\"> "
        "(no se puede detectar con el schema actual)."
    )
    fix_guidance = (
        "Conserva un único <meta name=\"description\"> en el <head>. "
        "Elimina cualquier duplicado introducido por plugins o templates."
    )
    references = [
        "https://developers.google.com/search/docs/appearance/snippet",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — schema does not track meta description count yet
        return iter([])
