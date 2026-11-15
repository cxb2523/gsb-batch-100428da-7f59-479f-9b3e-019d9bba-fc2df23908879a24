"""Alt text used as H1 content.

# TODO(schema): the schema stores h1 only as plain VARCHAR[] (text content);
# we don't differentiate text nodes vs <img alt> inside an <h1>. Re-enable
# when the extractor records source-of-text per heading.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class H1AltText(Rule):
    id = "h1_alt_text"
    name = "Alt Text in H1"
    category = "Headings"
    severity = "info"
    description = (
        "El <h1> está formado únicamente por el alt de una imagen "
        "(no detectable con el schema actual)."
    )
    fix_guidance = (
        "Asegura que el <h1> contenga texto real además (o en lugar) del alt de "
        "una imagen. El alt no debe ser la única fuente del título principal."
    )
    references = [
        "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/Heading_Elements",
    ]
    enabled_by_default = False

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — schema does not separate text vs alt content in h1
        return iter([])
