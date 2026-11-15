"""JavaScript redirect detection.

# TODO(schema): requires a `redirect_type` column in `redirects` populated when
# the headless renderer detects window.location / history.replaceState
# triggered redirects.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class RedirectJavascript(Rule):
    id = "redirect_javascript"
    name = "JavaScript redirect"
    category = "Response Codes"
    severity = "info"
    description = "URL redirige mediante JavaScript (window.location, etc.)."
    fix_guidance = (
        "Migra a redirección 301 a nivel de servidor siempre que sea posible. "
        "Google maneja redirecciones JS pero con coste y riesgo: requieren render, "
        "consumen crawl budget y pueden no transmitir señales si fallan."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics",
    ]
    enabled_by_default = False
    requires_rendered_html = True

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — schema does not yet differentiate redirect types
        return iter([])
