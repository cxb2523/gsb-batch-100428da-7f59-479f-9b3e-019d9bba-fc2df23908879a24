from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class CanonicalToHomepage(Rule):
    id = "canonical_to_homepage"
    name = "Canonical Points To Homepage"
    category = "Canonicals"
    severity = "warning"
    description = "El canonical apunta a la home del sitio desde una URL interna distinta."
    fix_guidance = (
        "Canonicalizar páginas internas a la home casi siempre es un error "
        "de configuración (típico de plantillas globales). Apunta cada "
        "página a su URL canónica real, no a la home."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # canonical is the homepage shape (scheme://host or scheme://host/),
        # but the page itself is NOT a homepage.
        rows = con.execute(
            r"""
            SELECT url_id, url, canonical
            FROM urls
            WHERE canonical IS NOT NULL
              AND canonical <> url
              AND regexp_matches(canonical, '^https?://[^/]+/?$')
              AND NOT regexp_matches(url, '^https?://[^/]+/?$')
            """
        ).fetchall()
        for url_id, url, canonical in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "canonical": canonical},
                message=f"Canonical apunta a la home: {canonical}",
            )
