from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class CanonicalToDisallowed(Rule):
    id = "canonical_to_disallowed"
    name = "Canonical Points To Disallowed"
    category = "Canonicals"
    severity = "warning"
    description = "El canonical apunta a una URL bloqueada por robots.txt."
    fix_guidance = (
        "Una URL bloqueada por robots.txt no puede ser leída por Google "
        "y por tanto no puede ser canónica. Apunta el canonical a una "
        "URL accesible o desbloquea la URL en robots.txt."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT a.url_id, a.url, a.canonical
            FROM urls a
            JOIN urls t ON t.url = a.canonical
            WHERE a.canonical IS NOT NULL
              AND a.canonical <> a.url
              AND t.from_robots = TRUE
            """
        ).fetchall()
        for url_id, url, canonical in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "canonical": canonical},
                message=f"Canonical bloqueado por robots.txt: {canonical}",
            )
