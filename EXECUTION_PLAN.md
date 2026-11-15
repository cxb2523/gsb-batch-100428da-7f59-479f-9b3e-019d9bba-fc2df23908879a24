# Plan de ejecución

## Fases

### Fase A — Serial (yo, agente único). 6-10h efectivas.

No paralelizable. Si dos agentes tocan el modelo de datos a la vez, conflicto.

1. **Scaffold** — pyproject, README, LICENSE, .gitignore, estructura ✅
2. **Schema DuckDB** — tablas urls, links, redirects, hreflang, structured_data, resources, issues. Documentar en `docs/SCHEMA.md`. **Esto es la API que consumen las 300 reglas, debe quedar fija.**
3. **Crawl engine base** — Scrapy spider + Playwright opt-in + persist a Parquet. CLI mínimo `crawlforge run <url>`.
4. **Rule API** — clase base `Rule`, decorador `@register_rule`, auto-discovery, contrato `check(con: DuckDBPyConnection) -> Iterable[Issue]`. Documentar en `docs/RULES_API.md`. **Esto fija el contrato para Fase B.**
5. **Smoke test E2E** — crawl de example.com → Parquet → 1 regla de ejemplo (`title_missing`) → output `Issue`.

### Fase B — Paralela (8-10 agentes). 15-25h efectivas.

Solo arrancar cuando Fase A pasa el smoke test.

**Estrategia de paralelización**: 1 categoría = 1 agente. Esto evita conflictos de archivo (cada agente solo toca `src/crawlforge/rules/<categoria>/`).

Categorías esperadas (a confirmar con catálogo del agente investigador):

| # | Categoría | Reglas estimadas | Agente |
|---|---|---|---|
| 1 | Response Codes | ~25 | rules_response_codes |
| 2 | URL | ~15 | rules_url |
| 3 | Page Titles | ~20 | rules_titles |
| 4 | Meta Description | ~15 | rules_meta |
| 5 | H1/H2/Headings | ~20 | rules_headings |
| 6 | Canonicals | ~25 | rules_canonicals |
| 7 | Hreflang | ~25 | rules_hreflang |
| 8 | Directives (robots/x-robots) | ~20 | rules_directives |
| 9 | Images | ~20 | rules_images |
| 10 | Structured Data | ~30 | rules_structured |
| 11 | Sitemaps | ~15 | rules_sitemaps |
| 12 | Security | ~20 | rules_security |
| 13 | Mobile/AMP | ~15 | rules_mobile |
| 14 | Performance / CWV | ~20 | rules_performance |
| 15 | Internal Linking / Architecture | ~20 | rules_architecture |
| 16 | Content / Duplicates | ~20 | rules_content |
| **Total** | | **~300** | |

Cada agente recibe:
- `docs/SCHEMA.md` (qué columnas hay disponibles)
- `docs/RULES_API.md` (contrato)
- `src/crawlforge/rules/_template.py` (esqueleto de regla)
- `tests/fixtures/sample_crawl.parquet` (BD de prueba con casos sembrados)
- Su sub-lista de reglas a implementar (extracto del catálogo investigador)

Cada regla = 1 archivo Python con:
- Decorador `@register_rule`
- Metadata (id, name, category, severity, description, fix_guidance)
- Método `check()` que devuelve issues
- Test unitario en `tests/unit/rules/<categoria>/test_<rule_id>.py`

### Fase C — Serial. 6-10h.

1. **MCP server** — FastMCP, tools: `crawl_run`, `query_urls`, `list_issues`, `find_redirect_chains`, `find_orphans`, `validate_hreflang`, `audit_summary`, `export_csv`.
2. **CLI completo** — `crawlforge run|query|audit|diff|export` con Typer + Rich.
3. **Reports HTML** — Jinja2 templates, audit overview, redirect chains, hreflang, structured data.
4. **Registro en Claude Code y Codex** — `claude mcp add crawlforge ...`.

### Fase D — Opcional, post-MVP.

- Crawl diff completo (cambios entre dos crawls)
- Integraciones GSC / GA4 / PSI con OAuth
- UI web ligera (Solid o SvelteKit, lectura de Parquet)
- Plantillas de auditoría por sector
- PyPI + Homebrew + Docker
- GitHub público

## Reglas de coordinación entre agentes

Aplicando la sección "Equipos de agentes paralelos" del CLAUDE.md global:

1. **UN ARCHIVO = UN SOLO AGENTE**. Cada agente solo toca su carpeta `rules/<categoria>/` y `tests/unit/rules/<categoria>/`.
2. **Archivos compartidos peligrosos** (NO tocar): `pyproject.toml`, `src/crawlforge/__init__.py`, `src/crawlforge/storage/schema.py`, `src/crawlforge/rules/__init__.py`, `docs/SCHEMA.md`, `docs/RULES_API.md`. Solo el agente coordinador (yo) los modifica.
3. **Mergear UNA POR UNA**. Verificar `pytest tests/unit/rules/<categoria>` después de cada merge.
4. **Si un agente reporta gap en el schema** (necesita una columna que no existe), parar Fase B, ampliar schema serialmente, reanudar.

## Estimación honesta

| Fase | Horas efectivas | Tipo |
|---|---|---|
| A | 6-10h | Serial |
| B | 15-25h | Paralela (real ~3-4h × 8 agentes) |
| C | 6-10h | Serial |
| **Total MVP funcional** | **30-50h** | |
| D | 40-80h | Mix |

**Calendario realista**: 4-8 días si hay sesiones largas. 2-3 semanas si es trabajo compartimentado entre otros proyectos.
