"""Internal Disallowed Links.

# TODO(schema): requires cross-referencing links target URLs against
robots.txt. The crawler currently does not mark links as
`target_disallowed_by_robots`.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class InternalDisallowedLinks(Rule):
    id = "internal_disallowed_links"
    name = "Internal Disallowed Links"
    category = "Directives"
    severity = "warning"
    description = (
        "La página enlaza internamente a URLs bloqueadas por robots.txt. "
        "Estos enlaces consumen budget y no aportan equity SEO."
    )
    fix_guidance = (
        "Revisa la arquitectura interna: si las URLs están bloqueadas a "
        "propósito, oculta los links del menú principal o usa nofollow."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — links table doesn't track from_robots cross-ref yet
        return iter([])
