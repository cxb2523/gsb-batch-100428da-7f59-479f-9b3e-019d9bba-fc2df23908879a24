from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class SitemapNonIndexableUrls(Rule):
    id = "sitemap_non_indexable_urls"
    name = "Non-Indexable URLs in Sitemap"
    category = "Sitemaps"
    severity = "warning"
    description = (
        "URLs no indexables aparecen en el sitemap.xml. El sitemap solo "
        "debería listar URLs canónicas e indexables."
    )
    fix_guidance = (
        "Elimina del sitemap toda URL con noindex, canonical hacia otra URL, "
        "redirect, 4xx/5xx o bloqueada por robots.txt."
    )
    references = [
        "https://www.sitemaps.org/protocol.html",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url, indexability_reason
            FROM urls
            WHERE COALESCE(from_sitemap, FALSE) = TRUE
              AND COALESCE(is_indexable, FALSE) = FALSE
            """
        ).fetchall()
        for url_id, url, reason in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={
                    "url": url,
                    "indexability_reason": reason,
                },
                message=f"URL no indexable en sitemap: {url}",
            )
