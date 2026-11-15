from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ViewportNoWidth(Rule):
    id = "viewport_no_width"
    name = "Viewport Without Width"
    category = "Mobile"
    severity = "warning"
    description = "Meta viewport presente pero sin parámetro 'width'."
    fix_guidance = (
        "El viewport debe declarar 'width=device-width' (recomendado) o un "
        "ancho específico. Sin width los navegadores móviles asumen 980px."
    )
    references = [
        "https://web.dev/articles/responsive-web-design-basics",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT url_id, url, viewport
            FROM urls
            WHERE status_code = 200
              AND COALESCE(content_type, '') LIKE 'text/html%'
              AND viewport IS NOT NULL
              AND lower(viewport) NOT LIKE '%width%'
            """
        ).fetchall()
        for url_id, url, viewport in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url, "viewport": viewport},
                message=f"Viewport sin width en {url}: {viewport!r}",
            )
