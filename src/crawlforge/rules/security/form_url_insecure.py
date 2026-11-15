"""Form URL Insecure.

# TODO(schema): requires a `forms` table (or at least form action extraction
# in the `links` table) which is not yet present. We don't extract form actions.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class FormUrlInsecure(Rule):
    id = "form_url_insecure"
    name = "Form URL Insecure"
    category = "Security"
    severity = "critical"
    description = "Formulario que envía datos a una URL HTTP (action insegura)."
    fix_guidance = (
        "Cambia el `action` del formulario para que apunte a una URL HTTPS. "
        "Nunca envíes datos sensibles por HTTP."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/Security",
    ]
    enabled_by_default = False  # awaiting schema column for form actions

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs `forms` table or form-action data
        return iter([])
