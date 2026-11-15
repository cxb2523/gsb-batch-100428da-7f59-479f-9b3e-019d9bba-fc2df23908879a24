from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class SchemaValidationWarnings(Rule):
    id = "schema_validation_warnings"
    name = "Structured Data Validation Warnings"
    category = "Structured Data"
    severity = "info"
    enabled_by_default = False  # TODO(schema): no diferenciamos errors vs warnings
    description = "Structured data con warnings (campos opcionales recomendados)."
    fix_guidance = (
        "Añade los campos opcionales recomendados para mejorar la calidad "
        "y elegibilidad para rich results."
    )
    references = [
        "https://schema.org/",
        "https://developers.google.com/search/docs/appearance/structured-data",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # TODO(schema): need separate warnings column distinct from errors
        return iter([])
