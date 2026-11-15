"""TODO(schema): requires explicit pagination relationship (rel=next/prev)
to know which URLs belong to a paginated sequence. Without that, a 404 on a
?page= URL might be a coincidence, not a broken pagination link."""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class PaginationNon200(Rule):
    id = "pagination_non_200"
    name = "Non-200 Pagination"
    category = "Pagination"
    severity = "warning"
    description = "URL declarada como página de una secuencia paginada con código no-200."
    fix_guidance = (
        "Asegúrate de que todas las páginas de una secuencia paginada "
        "responden 200. Una página intermedia con 404/500 rompe el "
        "descubrimiento de las siguientes."
    )
    references = [
        "https://developers.google.com/search/docs/specialty/ecommerce/pagination-and-incremental-page-loading",
    ]
    # TODO(schema): needs pagination_links table.
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        return iter([])
