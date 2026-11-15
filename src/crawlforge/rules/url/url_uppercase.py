from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlUppercase(Rule):
    id = "url_uppercase"
    name = "Uppercase"
    category = "URL"
    severity = "warning"
    description = (
        "Path de la URL contiene letras mayúsculas. Esto puede generar "
        "duplicados (la mayoría de servidores son case-sensitive en path)."
    )
    fix_guidance = (
        "Usa solo minúsculas en URLs. Configura el servidor para 301 redirect "
        "de versiones con mayúsculas a su versión lowercase."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # Quitar scheme + host (case-insensitive por DNS) y mirar solo path+query.
        # `regexp_replace(url, '^[^/]*//[^/]*', '')` elimina `https://example.com`.
        # Después comprobamos si contiene [A-Z].
        rows = con.execute(
            """
            SELECT url_id, url
            FROM urls
            WHERE regexp_matches(
                regexp_replace(url, '^[^/]*//[^/]*', ''),
                '[A-Z]'
            )
            """
        ).fetchall()
        for url_id, url in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url},
                message=f"URL con mayúsculas en path: {url}",
            )
