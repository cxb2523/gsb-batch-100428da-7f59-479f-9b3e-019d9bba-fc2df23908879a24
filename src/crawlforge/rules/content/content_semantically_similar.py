"""Content semantically similar rule.

# TODO(schema): requires sentence-transformer embeddings (SBERT) not yet
# available in the schema. Skipped for MVP.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ContentSemanticallySimilar(Rule):
    id = "content_semantically_similar"
    name = "Content Semantically Similar"
    category = "Content"
    severity = "info"
    description = "Páginas con significado comparable (requiere embeddings semánticos)."
    fix_guidance = (
        "Cuando esté disponible: revisa páginas semánticamente parecidas y considera "
        "consolidarlas o diferenciarlas con contenido único."
    )
    references = [
        "https://developers.google.com/search/docs/fundamentals/creating-helpful-content",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query: needs SBERT embeddings column
        return iter([])
