"""Low relevance content rule.

# TODO(schema): requires topical relevance scoring (TF-IDF / embeddings)
# not yet available.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class LowRelevanceContent(Rule):
    id = "low_relevance_content"
    name = "Low Relevance Content"
    category = "Content"
    severity = "info"
    description = "Páginas con contenido sin relevancia temática para el sitio."
    fix_guidance = "Revisa el contenido y alinéalo con la temática principal del sitio."
    references = [
        "https://developers.google.com/search/docs/fundamentals/creating-helpful-content",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder: needs topical relevance scoring
        return iter([])
