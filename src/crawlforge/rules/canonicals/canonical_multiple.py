"""TODO(schema): requires the count of <link rel=canonical> tags found in the
HTML (currently we only persist a single resolved value)."""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class CanonicalMultiple(Rule):
    id = "canonical_multiple"
    name = "Canonical Multiple"
    category = "Canonicals"
    severity = "warning"
    description = "Más de un <link rel=\"canonical\"> declarado en la misma página."
    fix_guidance = (
        "Deja exactamente un único <link rel=\"canonical\"> dentro del "
        "<head>. Si hay varios, Google ignora todos."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    ]
    # TODO(schema): needs urls.canonical_count or list of canonicals.
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        return iter([])
