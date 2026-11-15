from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HtmlLangMissing(Rule):
    id = "html_lang_missing"
    name = "Missing HTML lang attribute"
    category = "Hreflang"
    severity = "warning"
    description = (
        "El elemento <html> no declara el atributo lang. Esto afecta a "
        "accesibilidad y a la detección automática de idioma por buscadores."
    )
    fix_guidance = (
        "Añade lang al elemento raíz: <html lang=\"es\">. El valor debe "
        "coincidir con el idioma principal del contenido."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/international/localized-versions",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url
            FROM urls
            WHERE status_code = 200
              AND COALESCE(content_type, '') LIKE 'text/html%'
              AND (lang IS NULL OR trim(lang) = '')
            """
        ).fetchall()
        for url_id, url in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url},
                message=f"{url} no declara <html lang>",
            )
