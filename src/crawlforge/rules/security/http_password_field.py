"""HTTP password input.

# TODO(schema): requires extraction of <input type="password"> (no column yet).
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class HttpPasswordField(Rule):
    id = "http_password_field"
    name = "HTTP password input"
    category = "Security"
    severity = "critical"
    description = "Campo `<input type=\"password\">` servido por HTTP."
    fix_guidance = (
        "Migra la página a HTTPS antes de exponer cualquier campo de contraseña. "
        "Chrome/Firefox marcan estos formularios como inseguros."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/Security",
    ]
    enabled_by_default = False  # awaiting schema column for form input fields

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs password-field extraction
        return iter([])
