"""Mobile alternate link (<link rel='alternate' media='only screen and (max-width:...)'>).

# TODO(schema): requires `<link rel='alternate'>` non-hreflang variants.
The `hreflang` table only stores language alternates. Mobile-alternate links
(media-query alternates pointing to m.example.com style mobile sites) aren't
tracked separately.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class MobileAlternateLink(Rule):
    id = "mobile_alternate_link"
    name = "Mobile Alternate Link"
    category = "Mobile"
    severity = "info"
    description = (
        "Página declara <link rel='alternate' media='...'> hacia versión mobile. "
        "Patrón m. legacy: considera adoptar diseño responsive."
    )
    fix_guidance = (
        "El patrón m.example.com está deprecado. Migra a un sitio único con "
        "diseño responsive y elimina los links rel=alternate de mobile."
    )
    references = [
        "https://developers.google.com/search/mobile-sites/mobile-seo/separate-urls",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs <link rel=alternate media=> capture
        return iter([])
