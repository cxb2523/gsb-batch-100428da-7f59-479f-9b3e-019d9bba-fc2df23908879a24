from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class Canonicalised(Rule):
    id = "canonicalised"
    name = "Canonicalised"
    category = "Canonicals"
    severity = "info"
    description = "La URL declara un canonical distinto de sí misma."
    fix_guidance = (
        "Si la canonicalización es intencional (variante de URL, parámetros, "
        "etc.) no requiere acción. Si esperabas que esta URL fuera la "
        "canónica, revisa la etiqueta y apunta a sí misma."
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
              AND trim(canonical) <> ''
              AND canonical <> url
              AND canonical LIKE 'http%'
            """
        ).fetchall()
        for url_id, url, canonical in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "canonical": canonical},
                message=f"Canonicalizada hacia: {canonical}",
            )
