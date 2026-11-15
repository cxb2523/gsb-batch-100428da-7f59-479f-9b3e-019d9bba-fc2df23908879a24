"""Content technically duplicate rule.

# TODO(schema): the engine already canonicalizes URLs at crawl time (lowercase
# host, trailing slash normalisation, query-param ordering). The "raw vs
# canonical" pair is therefore not stored. Marking the rule as not yet
# implementable until that mapping is persisted.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ContentTechnicallyDuplicate(Rule):
    id = "content_technically_duplicate"
    name = "Content Technically Duplicate"
    category = "Content"
    severity = "warning"
    description = "Múltiples URLs (raw) que normalizan a la misma URL canónica."
    fix_guidance = (
        "Implementa redirects 301 desde las variantes (mayúsculas, trailing slash, "
        "orden de query params) hacia la versión canónica."
    )
    references = [
        "https://developers.google.com/search/docs/fundamentals/creating-helpful-content",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder: needs raw_url -> canonical_url mapping persisted
        return iter([])
