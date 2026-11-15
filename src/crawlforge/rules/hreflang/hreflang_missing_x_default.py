from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HreflangMissingXDefault(Rule):
    id = "hreflang_missing_x_default"
    name = "Hreflang Missing x-default"
    category = "Hreflang"
    severity = "info"
    description = (
        "Una página con anotaciones hreflang no incluye la entrada "
        "lang='x-default'."
    )
    fix_guidance = (
        "Considera añadir lang='x-default' apuntando a la versión que se "
        "mostrará por defecto cuando ningún lang/región coincide con el "
        "usuario (típicamente la versión inglesa o el selector de idioma)."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/international/localized-versions",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT u.url_id, u.url
            FROM urls u
            WHERE EXISTS (
                SELECT 1 FROM hreflang h WHERE h.source_url_id = u.url_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM hreflang h
                WHERE h.source_url_id = u.url_id AND h.lang = 'x-default'
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
                message=f"{url} declara hreflang pero falta 'x-default'",
            )
