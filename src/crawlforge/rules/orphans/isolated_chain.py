from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class IsolatedChain(Rule):
    id = "isolated_chain"
    name = "Isolated URL — Only Linked from Other Isolated URLs"
    category = "Orphans"
    severity = "warning"
    enabled_by_default = False  # TODO: requires recursive isolation analysis
    description = (
        "URL recibe enlaces solo desde otras URLs aisladas (cadena de "
        "aislamiento). Detección heurística pendiente."
    )
    fix_guidance = (
        "Identifica el cluster aislado y conecta al menos una URL al grafo "
        "principal con un link interno desde una página bien enlazada."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # TODO: requires recursive CTE that propagates isolation marker
        return iter([])
