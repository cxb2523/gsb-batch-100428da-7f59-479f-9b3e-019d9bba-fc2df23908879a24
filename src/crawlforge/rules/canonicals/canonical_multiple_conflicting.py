"""TODO(schema): requires list of all canonical values found in the HTML
to detect conflicting ones. The current schema persists a single value."""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class CanonicalMultipleConflicting(Rule):
    id = "canonical_multiple_conflicting"
    name = "Canonical Multiple Conflicting"
    category = "Canonicals"
    severity = "critical"
    description = "Múltiples canonicals en la misma página apuntando a URLs distintas."
    fix_guidance = (
        "Deja un único canonical apuntando a la URL preferida. Múltiples "
        "canonicals con destinos distintos invalidan toda la señal."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    ]
    # TODO(schema): needs urls.canonicals_all VARCHAR[] column.
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        return iter([])
