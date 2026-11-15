# Schema de datos

> **Esta es la API que consumen las 300 reglas. Cualquier cambio rompe Fase B.**
> Cualquier modificación tras congelar el schema requiere bump de `SCHEMA_VERSION` y migración.

## Filosofía

- **Una fila por URL** en `urls`. El resto son tablas relacionales (links, redirects, hreflang...).
- **Todos los campos extraíbles del HTML están en columnas tipadas** — las reglas no parsean HTML, solo hacen queries SQL sobre columnas ya extraídas. Esto desacopla extracción (engine) de detección (rules).
- **Datos crudos siempre disponibles**: el HTML completo se persiste en `urls.raw_html` (opcional, off por defecto en crawls grandes) y el HTML renderizado en `urls.rendered_html` (cuando JS rendering está activo).
- **Parquet en disco, DuckDB para queries**. Cada crawl es un directorio:
  ```
  crawls/mysite-2026-05-06/
    urls.parquet
    links.parquet
    redirects.parquet
    hreflang.parquet
    structured_data.parquet
    resources.parquet
    issues.parquet
    crawl_meta.json
  ```

## Tablas

### `urls` — una fila por URL crawleada
| Columna | Tipo | Descripción |
|---|---|---|
| url_id | UBIGINT | Hash estable (xxhash64 de la URL) |
| url | VARCHAR | URL canónica tras normalización |
| final_url | VARCHAR | URL tras seguir redirects |
| status_code | INTEGER | HTTP status de la respuesta final |
| redirect_count | INTEGER | Número de saltos de redirect (0 si directo) |
| content_type | VARCHAR | Header `Content-Type` |
| content_length | BIGINT | Bytes |
| response_time_ms | INTEGER | TTFB |
| depth | INTEGER | Distancia desde URL inicial (0 = root) |
| crawled_at | TIMESTAMP | Cuándo se crawleó |
| from_sitemap | BOOLEAN | ¿Apareció en sitemap? |
| from_robots | BOOLEAN | ¿Bloqueado por robots? |
| rendered | BOOLEAN | ¿Se renderizó con Playwright? |
| **HTML head** | | |
| title | VARCHAR | `<title>` |
| meta_description | VARCHAR | `<meta name="description">` |
| meta_robots | VARCHAR | `<meta name="robots">` (raw) |
| canonical | VARCHAR | `<link rel="canonical" href>` |
| viewport | VARCHAR | `<meta name="viewport">` |
| charset | VARCHAR | charset declarado |
| lang | VARCHAR | `<html lang>` |
| **Headings** | | |
| h1 | VARCHAR[] | Lista de H1 (puede haber varios) |
| h2 | VARCHAR[] | Lista de H2 |
| h3 | VARCHAR[] | Lista de H3 |
| h4 | VARCHAR[] | Lista de H4 |
| h5 | VARCHAR[] | Lista de H5 |
| h6 | VARCHAR[] | Lista de H6 |
| **Indexability** | | |
| is_indexable | BOOLEAN | Pasa todos los checks (canonical OK + 200 + no robots block + no noindex) |
| indexability_reason | VARCHAR | Si no indexable, motivo |
| x_robots_tag | VARCHAR | Header `X-Robots-Tag` |
| **Content** | | |
| word_count | INTEGER | Palabras en el body |
| text_to_html_ratio | DOUBLE | text_chars / total_chars |
| content_hash | VARCHAR | SHA256 del body normalizado (para exact duplicates) |
| simhash | UBIGINT | Simhash 64-bit (para near-duplicates) |
| **Performance (si rendered)** | | |
| lcp_ms | INTEGER | NULL si no medido |
| cls | DOUBLE | NULL si no medido |
| inp_ms | INTEGER | NULL si no medido |
| **Headers de seguridad** | | |
| header_csp | VARCHAR | Content-Security-Policy |
| header_hsts | VARCHAR | Strict-Transport-Security |
| header_x_frame_options | VARCHAR | |
| header_referrer_policy | VARCHAR | |
| **Raw (opcional)** | | |
| raw_html | BLOB | NULL si no se persiste |
| rendered_html | BLOB | NULL si no se renderizó |

### `links` — relaciones URL → URL (internal + external)
| Columna | Tipo | Descripción |
|---|---|---|
| source_url_id | UBIGINT | URL que contiene el link |
| target_url | VARCHAR | URL destino (sin resolver todavía) |
| target_url_id | UBIGINT | NULL si target no se crawleó (external u orphan) |
| anchor | VARCHAR | Texto del anchor |
| rel | VARCHAR | Atributo `rel` (nofollow, sponsored, ugc, etc.) |
| link_type | VARCHAR | a / link / form / iframe |
| in_navigation | BOOLEAN | ¿Dentro de `<nav>`? |
| in_footer | BOOLEAN | |
| position | INTEGER | Orden de aparición en el DOM |

### `redirects` — cadenas de redirección
| Columna | Tipo | Descripción |
|---|---|---|
| chain_id | UBIGINT | Hash de la cadena completa |
| from_url | VARCHAR | |
| to_url | VARCHAR | |
| status_code | INTEGER | 301/302/307/308/meta-refresh |
| hop | INTEGER | Posición en la cadena (0 = primera) |
| is_loop | BOOLEAN | Detección de bucle |

### `hreflang` — declaraciones hreflang
| Columna | Tipo | Descripción |
|---|---|---|
| source_url_id | UBIGINT | |
| lang | VARCHAR | `en-US`, `x-default`, etc. |
| href | VARCHAR | URL declarada |
| href_url_id | UBIGINT | NULL si href no crawleado |
| from_html | BOOLEAN | Vino de `<link rel=alternate>` |
| from_header | BOOLEAN | Vino de header `Link:` |
| from_sitemap | BOOLEAN | Vino de sitemap.xml |

### `structured_data` — JSON-LD / Microdata / RDFa extraídos
| Columna | Tipo | Descripción |
|---|---|---|
| url_id | UBIGINT | |
| syntax | VARCHAR | json-ld / microdata / rdfa / opengraph / microformat |
| schema_type | VARCHAR | `Product`, `Article`, `BreadcrumbList`... |
| data | JSON | El blob completo extraído |
| valid | BOOLEAN | Pasó validación schema.org |
| validation_errors | VARCHAR[] | Lista de errores si valid=false |

### `resources` — imágenes, scripts, css referenciados
| Columna | Tipo | Descripción |
|---|---|---|
| source_url_id | UBIGINT | |
| resource_url | VARCHAR | |
| resource_type | VARCHAR | image / script / stylesheet / video / font / iframe |
| status | INTEGER | NULL si no se crawleó |
| size_bytes | BIGINT | |
| alt | VARCHAR | Solo para imágenes |
| width | INTEGER | |
| height | INTEGER | |
| loading_attr | VARCHAR | `lazy` / `eager` |
| format | VARCHAR | `webp` / `avif` / `png` / `jpg` |

### `issues` — output de las reglas (poblado por Fase B)
| Columna | Tipo | Descripción |
|---|---|---|
| issue_id | UBIGINT | Hash (rule_id + url_id + evidence) |
| rule_id | VARCHAR | `title_duplicate`, `canonical_loop`... |
| url_id | UBIGINT | NULL si es issue de site-level |
| severity | VARCHAR | critical / warning / info |
| category | VARCHAR | |
| evidence | JSON | Datos del caso (e.g. `{title: "...", duplicates: [url1, url2]}`) |
| message | VARCHAR | Texto humano accionable |
| detected_at | TIMESTAMP | |

### `crawl_meta.json` — metadata del crawl
```json
{
  "crawl_id": "mysite-2026-05-06",
  "config_path": "audits/mysite.yml",
  "started_at": "2026-05-06T10:30:00Z",
  "finished_at": "2026-05-06T10:54:18Z",
  "schema_version": "1.0.0",
  "crawlforge_version": "0.0.1",
  "urls_total": 1247,
  "urls_indexable": 1102,
  "urls_failed": 14,
  "issues_total": 287,
  "issues_critical": 12,
  "issues_warning": 95,
  "issues_info": 180
}
```

## Convenciones

- **URL canónica**: lowercase scheme + host, path sin trailing slash salvo root, query params ordenados alfabéticamente, fragment descartado.
- **url_id estable**: xxhash64(url_canónica). Permite joins entre crawls de fechas distintas.
- **Arrays nativos** en DuckDB para listas (h1[], h2[], validation_errors[]). No JSON-stringificado.
- **NULL semántica**: NULL = "no medido / no aplica". Para "medido y vacío" usar string vacío o array vacío.
- **TIMESTAMP** en UTC siempre.
- **VARCHAR sin length limit** (DuckDB lo permite, evita decisiones arbitrarias).
