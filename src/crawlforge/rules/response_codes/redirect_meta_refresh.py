"""Meta Refresh redirect detection.

# TODO(schema): requires a `redirect_type` column in `redirects` (values:
# http / meta-refresh / http-refresh / javascript). Currently `redirects.status_code`
# encodes only HTTP-level redirects (301/302/...).
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class RedirectMetaRefresh(Rule):
    id = "redirect_meta_refresh"
    name = "Meta Refresh redirect"
    category = "Response Codes"
    severity = "info"
    description = "URL utiliza meta refresh para redirigir (anti-patrón SEO)."
    fix_guidance = (
        "Sustituye el meta refresh por una redirección 301 a nivel de servidor. "
        "Google recomienda meta refresh solo en casos limitados; un 301 transmite "
        "señales de ranking de forma más fiable."
    )
    references = [
        "https://developers.google.com/search/docs/crawling-indexing/301-redirects",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — schema does not yet differentiate redirect types
        return iter([])
