from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class TitleMissing(Rule):
    id = "title_missing"
    name = "Title Missing"
    category = "Page Titles"
    severity = "critical"
    description = "URL devuelve 200 pero no tiene <title> o está vacío."
    fix_guidance = (
        "Añade un <title> único y descriptivo de 30-60 caracteres "
        "que refleje la intención de búsqueda de la página."
    )
    references = [
        "https://developers.google.com/search/docs/appearance/title-link",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url
            FROM urls
            WHERE status_code = 200
              AND COALESCE(content_type, '') LIKE 'text/html%'
              AND (title IS NULL OR trim(title) = '')
            """
        ).fetchall()
        for url_id, url in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url},
                message=f"Página sin <title>: {url}",
            )
