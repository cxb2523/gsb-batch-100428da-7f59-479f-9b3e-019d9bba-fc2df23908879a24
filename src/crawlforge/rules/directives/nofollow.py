from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class Nofollow(Rule):
    id = "nofollow"
    name = "Nofollow"
    category = "Directives"
    severity = "info"
    description = (
        "URL declara `nofollow` (vía meta robots o X-Robots-Tag), por lo que "
        "los buscadores no seguirán los enlaces salientes desde esta página."
    )
    fix_guidance = (
        "Si la página enlaza a contenido propio que quieres rastrear, elimina "
        "`nofollow`. Solo aplícalo a páginas con muchos enlaces externos no "
        "garantizados (UGC, foros, etc.)."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url, meta_robots, x_robots_tag
            FROM urls
            WHERE (LOWER(COALESCE(meta_robots, '')) LIKE '%nofollow%'
                OR LOWER(COALESCE(x_robots_tag, '')) LIKE '%nofollow%')
            """
        ).fetchall()
        for url_id, url, meta_robots, x_robots_tag in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={
                    "url": url,
                    "meta_robots": meta_robots,
                    "x_robots_tag": x_robots_tag,
                },
                message=f"URL con nofollow: {url}",
            )
