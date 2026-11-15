from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue

_PATTERN = r"^([a-zA-Z]{2,3})(-[a-zA-Z]{2,4})?(-[a-zA-Z0-9]+)*$"


@register_rule
class HtmlLangInvalid(Rule):
    id = "html_lang_invalid"
    name = "Invalid HTML lang attribute"
    category = "Hreflang"
    severity = "warning"
    description = (
        "El atributo lang del elemento <html> tiene un formato inválido."
    )
    fix_guidance = (
        "Usa un código ISO 639-1 (2-3 letras), opcionalmente seguido de "
        "región (en-US, es-MX...). Para variantes regionales acepta también "
        "subcódigos extendidos (zh-Hans, sr-Latn-RS)."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/international/localized-versions",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            f"""
            SELECT url_id, url, lang
            FROM urls
            WHERE status_code = 200
              AND COALESCE(content_type, '') LIKE 'text/html%'
              AND lang IS NOT NULL
              AND trim(lang) <> ''
              AND NOT regexp_matches(lang, '{_PATTERN}')
            """
        ).fetchall()
        for url_id, url, lang in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "lang": lang},
                message=f"{url} declara lang inválido: {lang!r}",
            )
