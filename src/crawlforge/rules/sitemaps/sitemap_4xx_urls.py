from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class Sitemap4xxUrls(Rule):
    id = "sitemap_4xx_urls"
    name = "4XX URL in Sitemap"
    category = "Sitemaps"
    severity = "critical"
    description = (
        "Una URL del sitemap.xml devuelve un código 4xx (cliente). Esto "
        "desperdicia crawl budget y degrada la confianza en el sitemap."
    )
    fix_guidance = (
        "Elimina inmediatamente del sitemap.xml las URLs que devuelven 4xx, "
        "o restaura el contenido si la URL existía y se rompió."
    )
    references = [
        "https://www.sitemaps.org/protocol.html",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url, status_code
            FROM urls
            WHERE COALESCE(from_sitemap, FALSE) = TRUE
              AND status_code BETWEEN 400 AND 499
            """
        ).fetchall()
        for url_id, url, status in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "status_code": status},
                message=f"URL {status} en sitemap: {url}",
            )
