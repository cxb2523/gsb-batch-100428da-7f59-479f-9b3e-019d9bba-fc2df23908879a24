from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HreflangMultipleMethods(Rule):
    id = "hreflang_multiple_methods"
    name = "Hreflang Declared via Multiple Methods"
    category = "Hreflang"
    severity = "info"
    description = (
        "La misma página declara hreflang vía más de un método "
        "(HTML + Header HTTP + Sitemap)."
    )
    fix_guidance = (
        "Google recomienda usar un único método para evitar inconsistencias. "
        "Mantén el método que mejor encaja con tu setup y elimina los demás."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/international/localized-versions",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT h.source_url_id,
                   src.url AS source_url,
                   bool_or(h.from_html) AS in_html,
                   bool_or(h.from_header) AS in_header,
                   bool_or(h.from_sitemap) AS in_sitemap
            FROM hreflang h
            LEFT JOIN urls src ON src.url_id = h.source_url_id
            GROUP BY h.source_url_id, src.url
            HAVING (
                CAST(bool_or(h.from_html) AS INTEGER)
                + CAST(bool_or(h.from_header) AS INTEGER)
                + CAST(bool_or(h.from_sitemap) AS INTEGER)
            ) > 1
            """
        ).fetchall()
        for source_url_id, source_url, in_html, in_header, in_sitemap in rows:
            methods = [
                m
                for m, present in (
                    ("html", in_html),
                    ("header", in_header),
                    ("sitemap", in_sitemap),
                )
                if present
            ]
            yield Issue(
                rule_id=self.id,
                url_id=source_url_id,
                severity=self.severity,
                category=self.category,
                evidence={
                    "source_url": source_url,
                    "methods": methods,
                },
                message=(
                    f"{source_url} declara hreflang vía {methods!r}"
                ),
            )
