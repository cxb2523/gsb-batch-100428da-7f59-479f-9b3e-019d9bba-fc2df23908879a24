"""TODO(schema): requires rel=next/prev metadata to detect a single URL
participating in multiple paginated sequences."""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class PaginationMultiple(Rule):
    id = "pagination_multiple"
    name = "Multiple Pagination URLs"
    category = "Pagination"
    severity = "info"
    description = "Una URL participa en múltiples secuencias de paginación."
    fix_guidance = (
        "Una URL debería pertenecer a una única secuencia paginada. "
        "Múltiples rel=next/prev conflictivos confunden a los crawlers."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/ecommerce/pagination-and-incremental-page-loading",
    ]
    # TODO(schema): needs pagination_links table.
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        return iter([])
