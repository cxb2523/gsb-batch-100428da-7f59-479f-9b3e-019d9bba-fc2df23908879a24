from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HeadingEmpty(Rule):
    id = "heading_empty"
    name = "Heading Empty"
    category = "Headings"
    severity = "info"
    description = "Algún encabezado h1..h6 contiene únicamente espacios o cadena vacía."
    fix_guidance = (
        "Elimina los encabezados vacíos o rellénalos con texto descriptivo. "
        "Encabezados vacíos confunden a lectores de pantalla y crawlers."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/Heading_Elements",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url, level, empty_count FROM (
                SELECT
                    url_id, url,
                    1 AS level,
                    len(list_filter(h1, x -> x IS NOT NULL AND trim(x) = '')) AS empty_count
                FROM urls WHERE status_code = 200 AND h1 IS NOT NULL
                UNION ALL
                SELECT url_id, url, 2,
                    len(list_filter(h2, x -> x IS NOT NULL AND trim(x) = ''))
                FROM urls WHERE status_code = 200 AND h2 IS NOT NULL
                UNION ALL
                SELECT url_id, url, 3,
                    len(list_filter(h3, x -> x IS NOT NULL AND trim(x) = ''))
                FROM urls WHERE status_code = 200 AND h3 IS NOT NULL
                UNION ALL
                SELECT url_id, url, 4,
                    len(list_filter(h4, x -> x IS NOT NULL AND trim(x) = ''))
                FROM urls WHERE status_code = 200 AND h4 IS NOT NULL
                UNION ALL
                SELECT url_id, url, 5,
                    len(list_filter(h5, x -> x IS NOT NULL AND trim(x) = ''))
                FROM urls WHERE status_code = 200 AND h5 IS NOT NULL
                UNION ALL
                SELECT url_id, url, 6,
                    len(list_filter(h6, x -> x IS NOT NULL AND trim(x) = ''))
                FROM urls WHERE status_code = 200 AND h6 IS NOT NULL
            )
            WHERE empty_count > 0
            ORDER BY url_id, level
            """
        ).fetchall()
        for url_id, url, level, empty_count in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={
                    "url": url,
                    "level": f"h{level}",
                    "empty_count": empty_count,
                },
                message=f"{empty_count} <h{level}> vacíos en {url}",
            )
