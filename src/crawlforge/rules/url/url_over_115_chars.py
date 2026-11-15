from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlOver115Chars(Rule):
    id = "url_over_115_chars"
    name = "Over 115 Characters"
    category = "URL"
    severity = "info"
    description = (
        "URL excede 115 caracteres. URLs cortas son más memorables, "
        "compartibles y se truncan menos en SERPs."
    )
    fix_guidance = (
        "Acorta el path eliminando palabras innecesarias y elimina "
        "parámetros redundantes. Mantén URLs por debajo de ~80 caracteres "
        "cuando sea posible."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url, length(url) AS len
            FROM urls
            WHERE length(url) > 115
            """
        ).fetchall()
        for url_id, url, length in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "length": length},
                message=f"URL de {length} caracteres (> 115): {url}",
            )
