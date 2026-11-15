from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HreflangInvalidCodes(Rule):
    id = "hreflang_invalid_codes"
    name = "Hreflang Invalid Language/Region Codes"
    category = "Hreflang"
    severity = "critical"
    description = (
        "Una anotación hreflang usa un código de idioma/región con formato "
        "inválido (no cumple ISO 639-1 / ISO 3166-1 ni 'x-default')."
    )
    fix_guidance = (
        "Usa códigos ISO 639-1 (lang, 2-3 letras minúsculas) opcionalmente "
        "seguidos de '-' + ISO 3166-1 alpha-2 (región, 2 letras mayúsculas), "
        "p.ej. 'en', 'en-US', 'es-419', o el especial 'x-default'."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/international/localized-versions",
    ]

    _PATTERN = r"^([a-z]{2,3})(-[A-Z]{2})?$"

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            f"""
            SELECT source_url_id, lang, href
            FROM hreflang
            WHERE lang IS NULL
               OR (lang <> 'x-default' AND NOT regexp_matches(lang, '{self._PATTERN}'))
            """
        ).fetchall()
        for source_url_id, lang, href in rows:
            yield Issue(
                rule_id=self.id,
                url_id=source_url_id,
                severity=self.severity,
                category=self.category,
                evidence={"lang": lang, "href": href},
                message=f"Código hreflang inválido: {lang!r} (href={href})",
            )
