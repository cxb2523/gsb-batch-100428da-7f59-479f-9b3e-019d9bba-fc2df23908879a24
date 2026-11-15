"""External Blocked Resource.

# TODO(schema): requires crawling external resources and storing their robots
# decision. Today externals are not crawled by default.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ExternalBlockedResource(Rule):
    id = "external_blocked_resource"
    name = "External Blocked Resource"
    category = "Response Codes"
    severity = "warning"
    description = "Recurso externo (CSS/JS/imagen) bloqueado por robots.txt del dominio externo."
    fix_guidance = (
        "Si tu página depende de un recurso externo bloqueado para Googlebot, considera "
        "auto-hospedar el recurso o cambiar a un proveedor que sí permita crawling."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — externals not crawled
        return iter([])
