from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ImageMissingAltText(Rule):
    id = "image_missing_alt_text"
    name = "Image Missing Alt Text"
    category = "Images"
    severity = "warning"
    description = (
        "Imagen tiene atributo alt presente pero vacío "
        "(alt='') sin justificación decorativa explícita."
    )
    fix_guidance = (
        "Si la imagen es informativa, añade un alt descriptivo (≤100 caracteres) "
        "que explique el contenido o función. Si es puramente decorativa y debe "
        "quedar vacía, márkala con role='presentation' o aria-hidden."
    )
    references = [
        "https://web.dev/articles/serve-images-webp",
        "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/img",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute(
            """
            SELECT r.source_url_id, r.resource_url, u.url
            FROM resources r
            LEFT JOIN urls u ON u.url_id = r.source_url_id
            WHERE r.resource_type = 'image'
              AND r.alt IS NOT NULL
              AND trim(r.alt) = ''
            """
        ).fetchall()
        for source_url_id, resource_url, page_url in rows:
            yield Issue(
                rule_id=self.id,
                url_id=source_url_id,
                severity=self.severity,
                category=self.category,
                evidence={"page_url": page_url, "image_url": resource_url},
                message=f"Imagen con alt vacío en {page_url}: {resource_url}",
            )
