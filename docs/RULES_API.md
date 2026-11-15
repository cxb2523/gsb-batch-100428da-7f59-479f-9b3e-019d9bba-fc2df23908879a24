# Rule API — contrato para Fase B

> Cada regla es un archivo Python autocontenido en `src/crawlforge/rules/<categoria>/<rule_id>.py`.
> Esta API es el **contrato inmutable** que paraleliza Fase B.

## Esqueleto de una regla

```python
# src/crawlforge/rules/page_titles/title_missing.py
from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class TitleMissing(Rule):
    id = "title_missing"
    name = "Title Missing"
    category = "Page Titles"
    severity = "critical"
    description = "URL devuelve 200 pero no tiene <title> o está vacío."
    fix_guidance = (
        "Añade un <title> único y descriptivo de 30-60 caracteres "
        "que refleje la intención de búsqueda principal de la página."
    )
    references = [
        "https://developers.google.com/search/docs/appearance/title-link",
    ]

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute("""
            SELECT url_id, url
            FROM urls
            WHERE status_code = 200
              AND content_type LIKE 'text/html%'
              AND (title IS NULL OR trim(title) = '')
        """).fetchall()

        for url_id, url in rows:
            yield Issue(
                rule_id=self.id,
                url_id=url_id,
                severity=self.severity,
                category=self.category,
                evidence={"url": url},
                message=f"Página sin <title>: {url}",
            )
```

## Contrato `Rule`

```python
class Rule:
    id: str                  # snake_case, único globalmente
    name: str                # nombre humano
    category: str            # debe coincidir con una de las 16 categorías oficiales
    severity: Literal["critical", "warning", "info"]
    description: str         # 1 línea, qué detecta
    fix_guidance: str        # 1-3 líneas, cómo arreglar (copy listo para cliente)
    references: list[str]    # URLs to primary docs that justify the rule (Google Search Central, MDN, web.dev, schema.org, W3C, sitemaps.org)
    enabled_by_default: bool = True
    requires_rendered_html: bool = False  # solo evaluable si JS rendering activo

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]: ...
```

## Decorador `@register_rule`

Auto-registro en `crawlforge.rules.REGISTRY: dict[str, Rule]`. La importación
de `src/crawlforge/rules/<categoria>/__init__.py` hace `from . import *` para
que el módulo se carga al importar la categoría.

## Test obligatorio por regla

```python
# tests/unit/rules/page_titles/test_title_missing.py
import duckdb
import pytest

from crawlforge.rules.page_titles.title_missing import TitleMissing


@pytest.fixture
def con():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE urls AS SELECT * FROM (VALUES
            (1, 'https://a.com/', 200, 'text/html', NULL),
            (2, 'https://a.com/b', 200, 'text/html', ''),
            (3, 'https://a.com/c', 200, 'text/html', 'OK title'),
            (4, 'https://a.com/d', 404, 'text/html', NULL),
        ) AS t(url_id, url, status_code, content_type, title);
    """)
    return con


def test_detects_missing_and_empty_titles(con):
    rule = TitleMissing()
    issues = list(rule.check(con))

    assert len(issues) == 2
    assert {i.evidence["url"] for i in issues} == {
        "https://a.com/",
        "https://a.com/b",
    }


def test_does_not_flag_non_html_or_non_200(con):
    # 404 no se evalúa aunque tenga title NULL
    rule = TitleMissing()
    issues = list(rule.check(con))
    assert all(i.url_id != 4 for i in issues)
```

## Reglas con dependencias entre URLs (cluster rules)

Algunas reglas (`canonical_loop`, `hreflang_missing_return_link`, `orphan_pages`, `near_duplicate`, `redirect_loop`) requieren cruzar varias URLs. Sigue el mismo contrato — la query SQL es más compleja, eso es todo.

```python
# src/crawlforge/rules/hreflang/missing_return_link.py
@register_rule
class HreflangMissingReturnLink(Rule):
    id = "hreflang_missing_return_link"
    name = "Hreflang Missing Return Link"
    category = "Hreflang"
    severity = "warning"
    description = "URL declara hreflang a otra URL que no apunta de vuelta."

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        rows = con.execute("""
            SELECT
                a.source_url_id AS source,
                a.href AS declared,
                a.lang
            FROM hreflang a
            LEFT JOIN hreflang b
              ON b.source_url_id = a.href_url_id
              AND b.href_url_id = a.source_url_id
            WHERE a.href_url_id IS NOT NULL
              AND b.source_url_id IS NULL
        """).fetchall()
        for source, declared, lang in rows:
            yield Issue(
                rule_id=self.id,
                url_id=source,
                severity=self.severity,
                category=self.category,
                evidence={"declared_to": declared, "lang": lang},
                message=f"Hreflang declarado a {declared} ({lang}) sin link de vuelta",
            )
```

## Reglas que NO debes implementar como Rule

- **Validación schema.org de structured data**: ya se hace en el engine al extraer (la columna `structured_data.valid`). Una Rule solo proyecta esos errores.
- **Cálculo de simhash / content_hash**: lo hace el engine. Las reglas solo lo usan en queries.
- **Detección de redirect chains**: ya está en `redirects.hop` y `redirects.is_loop`. Las reglas solo proyectan.

**Regla de oro**: si tu regla necesita parsear HTML, está mal. Pídele al engine que extraiga la columna que te falta.

## Severidades — guía

- **critical**: SEO fail directo o bloqueo de indexación. Ejemplos: 5xx, canonical loop, robots block + sitemap include.
- **warning**: degradación detectable. Ejemplos: title duplicate, missing alt, hreflang sin return link.
- **info**: best practice u optimización. Ejemplos: title >65 chars, ratio texto/HTML bajo, imagen no en webp.

## Naming conventions

- `id` en snake_case, sin prefijo de categoría: `title_missing`, no `pagetitles_title_missing`.
- `name` en Title Case humano: "Title Missing".
- Archivos en `src/crawlforge/rules/<categoria_snake>/<rule_id>.py`.
- Tests en `tests/unit/rules/<categoria_snake>/test_<rule_id>.py`.
- Una clase por archivo. Un test por clase mínimo (más si hay variantes).
