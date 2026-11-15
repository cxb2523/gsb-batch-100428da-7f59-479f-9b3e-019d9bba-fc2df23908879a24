from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class CanonicalToHttps(Rule):
    id = "canonical_to_https"
    name = "Canonical Points To HTTPS"
    category = "Canonicals"
    severity = "info"
    description = "Página HTTP con canonical apuntando a la versión HTTPS."
    fix_guidance = (
        "Es la dirección correcta de canonicalización (HTTP -> HTTPS), "
        "pero idealmente la versión HTTP debería redirigir 301 a HTTPS "
        "en lugar de servirse con un canonical. Considera implementar "
        "una redirección permanente."
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
              AND canonical LIKE 'https://%'
              AND url LIKE 'http://%'
            """
        ).fetchall()
        for url_id, url, canonical in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "canonical": canonical},
                message=f"Canonical HTTP -> HTTPS: {canonical}",
            )
