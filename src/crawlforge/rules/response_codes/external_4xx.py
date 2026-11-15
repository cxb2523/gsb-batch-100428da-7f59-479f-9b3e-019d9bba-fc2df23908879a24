"""External 4XX.

# TODO(schema): requires crawling external links to obtain their status code.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class External4xx(Rule):
    id = "external_4xx"
    name = "External 4XX"
    category = "Response Codes"
    severity = "warning"
    description = "Enlace saliente que apunta a una URL externa con error 4XX."
    fix_guidance = (
        "El destino externo devuelve error de cliente (404, 410...). Actualiza el enlace "
        "a una URL válida del mismo dominio o elimínalo. Demasiados enlaces rotos hacia "
        "fuera son señal de baja calidad."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — externals not crawled
        return iter([])
