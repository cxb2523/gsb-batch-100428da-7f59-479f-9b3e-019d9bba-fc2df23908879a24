"""Protocol-Relative Resource.

# TODO(schema): we currently don't preserve raw `//host/path` literals — the
# crawler resolves them to absolute URLs before storing. To detect them we'd
# need to either keep the raw href on `resources` or re-parse `raw_html`.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ProtocolRelativeResource(Rule):
    id = "protocol_relative_resource"
    name = "Protocol-Relative Resource"
    category = "Security"
    severity = "info"
    description = (
        "Recurso referenciado con URL protocol-relative `//host/...`. "
        "Práctica obsoleta hoy que todo es HTTPS."
    )
    fix_guidance = (
        "Reemplaza `//cdn.ejemplo.com/...` por `https://cdn.ejemplo.com/...`."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/Security",
    ]
    enabled_by_default = False  # raw protocol-relative literals not stored

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder — see TODO above
        return iter([])
