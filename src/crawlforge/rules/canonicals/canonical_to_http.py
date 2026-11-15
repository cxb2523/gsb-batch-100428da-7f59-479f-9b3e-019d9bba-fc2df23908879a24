from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class CanonicalToHttp(Rule):
    id = "canonical_to_http"
    name = "Canonical Points To HTTP"
    category = "Canonicals"
    severity = "critical"
    description = "Página HTTPS con canonical apuntando a la versión HTTP."
    fix_guidance = (
        "Cambia el canonical a la versión HTTPS. Apuntar HTTPS -> HTTP "
        "indica a Google que prefieres la versión insegura, lo que "
        "perjudica posicionamiento y puede provocar mixed content."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url, canonical
            FROM urls
            WHERE canonical IS NOT NULL
              AND canonical LIKE 'http://%'
              AND url LIKE 'https://%'
            """
        ).fetchall()
        for url_id, url, canonical in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "canonical": canonical},
                message=f"Canonical HTTPS -> HTTP: {canonical}",
            )
