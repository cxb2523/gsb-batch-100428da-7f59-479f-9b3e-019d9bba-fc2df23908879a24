from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue
from crawlforge.rules.amp._helpers import AMP_URL_SQL


@register_rule
class AmpUrlDisallowed(Rule):
    id = "amp_url_disallowed"
    name = "AMP URL is Disallowed by robots.txt"
    category = "AMP"
    severity = "warning"
    description = "Página AMP bloqueada por robots.txt."
    fix_guidance = "Permite el rastreo de las URLs AMP en robots.txt."
    references = [
        "https://amp.dev/documentation/guides-and-tutorials/start/create/",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            f"""
            SELECT url_id, url
            FROM urls
            WHERE from_robots = TRUE AND {AMP_URL_SQL}
            """
        ).fetchall()
        for url_id, url in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url},
                message=f"AMP {url} bloqueada por robots.txt",
            )
