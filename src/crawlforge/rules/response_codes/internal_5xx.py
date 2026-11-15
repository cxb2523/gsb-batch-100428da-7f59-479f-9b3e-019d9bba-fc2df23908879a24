from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class Internal5xx(Rule):
    id = "internal_5xx"
    name = "Internal Server Error (5XX)"
    category = "Response Codes"
    severity = "critical"
    description = "URL interna devuelve un código de error 5XX (servidor)."
    fix_guidance = (
        "Investiga el log del servidor: el backend está fallando al servir esta URL. "
        "Causas comunes: timeouts de DB, OOM, errores de aplicación. Resolver con urgencia "
        "porque Google trata 5XX como problema serio de calidad del sitio."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url, status_code
            FROM urls
            WHERE status_code BETWEEN 500 AND 599
            """
        ).fetchall()
        for url_id, url, status in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "status_code": status},
                message=f"URL interna {status}: {url}",
            )
