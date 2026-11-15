from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class IsolatedViaCanonical(Rule):
    id = "isolated_via_canonical"
    name = "Isolated URL — Only Found via Canonical"
    category = "Orphans"
    severity = "warning"
    description = (
        "URL solo descubierta porque otra página la cita en su canonical, "
        "pero sin enlaces internos directos."
    )
    fix_guidance = (
        "Añade enlaces internos directos a esta URL desde páginas relevantes; "
        "ser referenciada solo vía canonical no transmite link equity."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT DISTINCT u.url_id, u.url
            FROM urls u
            LEFT JOIN links l ON l.target_url_id = u.url_id
            JOIN urls src ON src.canonical = u.url AND src.url_id <> u.url_id
            WHERE l.target_url_id IS NULL
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
                message=f"{url} solo es accesible vía canonical desde otra URL",
            )
