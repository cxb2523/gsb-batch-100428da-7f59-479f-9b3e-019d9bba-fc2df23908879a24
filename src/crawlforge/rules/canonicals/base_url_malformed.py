"""TODO(schema): requires extraction of `<base href>` tags."""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class BaseUrlMalformed(Rule):
    id = "base_url_malformed"
    name = "Base URL Malformed"
    category = "Canonicals"
    severity = "info"
    description = "La etiqueta <base href> está mal formada."
    fix_guidance = (
        "Corrige el atributo href de <base>: debe ser una URL absoluta "
        "y bien formada. Una <base> mal formada puede romper la "
        "resolución de TODAS las URLs relativas en la página."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    ]
    # TODO(schema): needs urls.base_url VARCHAR column.
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        return iter([])
