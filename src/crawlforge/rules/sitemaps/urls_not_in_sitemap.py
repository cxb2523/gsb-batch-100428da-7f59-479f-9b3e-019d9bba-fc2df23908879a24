from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlsNotInSitemap(Rule):
    id = "urls_not_in_sitemap"
    name = "URLs Not In Sitemap"
    category = "Sitemaps"
    severity = "info"
    description = (
        "URLs indexables que no aparecen en ningún sitemap.xml conocido. "
        "Pueden tardar más en descubrirse e indexarse."
    )
    fix_guidance = (
        "Añade las URLs indexables al sitemap.xml. El sitemap debe contener "
        "todas las URLs canónicas que quieras posicionar."
    )
    references = [
        "https://www.sitemaps.org/protocol.html",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url
            FROM urls
            WHERE is_indexable = TRUE
              AND COALESCE(from_sitemap, FALSE) = FALSE
              AND status_code = 200
            """
        ).fetchall()
        for url_id, url in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url},
                message=f"URL indexable fuera de sitemap: {url}",
            )
