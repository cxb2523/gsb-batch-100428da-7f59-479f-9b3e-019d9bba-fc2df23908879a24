from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class UrlGaTrackingParams(Rule):
    id = "url_ga_tracking_params"
    name = "GA Tracking Parameters"
    category = "URL"
    severity = "info"
    description = (
        "URL con parámetros de tracking (`utm_*`, `gclid`, `fbclid`, `_ga`, "
        "`mc_eid`, `mc_cid`, `yclid`, `msclkid`)."
    )
    fix_guidance = (
        "No enlaces internamente a URLs con UTMs (rompen las sesiones de GA). "
        "Para URLs externas con tracking, declara una canonical sin parámetros."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/url-structure",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url
            FROM urls
            WHERE regexp_matches(
                url,
                '[?&](utm_[a-z]+|gclid|fbclid|_ga|mc_eid|mc_cid|yclid|msclkid|igshid)='
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
                message=f"URL con parámetros de tracking: {url}",
            )
