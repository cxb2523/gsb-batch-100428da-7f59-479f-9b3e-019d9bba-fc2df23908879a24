from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class OrphanUrl(Rule):
    id = "orphan_url"
    name = "Orphan URL"
    category = "Orphans"
    severity = "critical"
    description = (
        "URL conocida (descubierta vía sitemap o crawl) pero sin enlaces "
        "internos entrantes desde otras páginas crawleadas."
    )
    fix_guidance = (
        "Si la URL debe ser indexable, añade enlaces internos relevantes "
        "desde otras páginas. Si no, retírala del sitemap o márcala noindex."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT u.url_id, u.url
            FROM urls u
            LEFT JOIN links l ON l.target_url_id = u.url_id
            WHERE l.target_url_id IS NULL
              AND COALESCE(u.depth, 0) > 0
              AND u.status_code = 200
            """
        ).fetchall()
        for url_id, url in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url},
                message=f"{url} es huérfana — sin enlaces internos entrantes",
            )
