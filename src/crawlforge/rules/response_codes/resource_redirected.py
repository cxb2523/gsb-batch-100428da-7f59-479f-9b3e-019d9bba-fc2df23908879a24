"""Redirected resource.

# TODO(schema): requires resource-level redirect tracking.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ResourceRedirected(Rule):
    id = "resource_redirected"
    name = "Redirected resource"
    category = "Response Codes"
    severity = "info"
    description = "Recurso (imagen/CSS/JS) que pasa por una redirección antes de servirse."
    fix_guidance = (
        "Actualiza la URL del recurso en el HTML para apuntar a la versión final, "
        "evitando latencia extra del redirect en cada visita."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs resource-level redirect tracking
        return iter([])
