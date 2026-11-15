# Rules Catalog — ~330 SEO rules (industry standard)

> Master catalog of well-known technical SEO checks, compiled from public web
> standards and primary documentation: Google Search Central, MDN Web Docs,
> web.dev, Schema.org, W3C, and sitemaps.org. Each rule maps to one Python
> module under `src/crawlforge/rules/`.

**Severidad sugerida**:
- **critical**: bloquea indexación, rompe el sitio, o arruina la SERP visible.
- **warning**: degrada SEO de forma significativa pero no fatal.
- **info**: oportunidad de mejora / observación.

**⚠️ marca reglas que requieren cruce entre URLs** (no resolubles con regex sobre una sola página).

---

## Resumen por categoría

| # | Categoría | Reglas | Carpeta `rules/` |
|---|---|---|---|
| 1 | Response Codes / Status | 27 | `response_codes/` |
| 2 | Security | 14 | `security/` |
| 3 | URL | 21 | `url/` |
| 4 | Page Titles | 10 | `page_titles/` |
| 5 | Meta Description | 9 | `meta_description/` |
| 6 | H1/H2/Headings | 14 | `headings/` |
| 7 | Content | 15 | `content/` |
| 8 | Images | 9 | `images/` |
| 9 | Canonicals | 31 | `canonicals/` |
| 10 | Pagination | 7 | `pagination/` |
| 11 | Directives | 28 | `directives/` |
| 12 | Hreflang / International | 33 | `hreflang/` |
| 13 | JavaScript / Rendered | 15 | `javascript/` |
| 14 | Links / Internal Linking | 19 | `links/` |
| 15 | Orphan / Isolated Pages | 5 | `orphans/` |
| 16 | Sitemaps XML | 12 | `sitemaps/` |
| 17 | Structured Data | 6 | `structured_data/` |
| 18 | PageSpeed / Performance | 28 | `performance/` |
| 19 | Mobile | 15 | `mobile/` |
| 20 | AMP | 30 | `amp/` |
| 21 | Search Traffic | 11 | `search_traffic/` |
| 22 | Accessibility (axe-core/WCAG) | ~95 | `accessibility/` |
| | **Total** | **~330** | |

---

## Top 5 reglas técnicamente más peliagudas (cross-URL)

Todas requieren un grafo de URLs construido durante el crawl + análisis cross-document.

1. ⚠️ **`hreflang_missing_return_links`** — Para cada hreflang saliente A→B con código `xx-YY`, verificar que B tiene un hreflang de vuelta a A con el mismo `xx-YY`. Implica almacenar todo el clúster de hreflang de cada URL y resolverlo solo después del crawl. Caso especial: hreflang vía sitemap ó HTTP header se mezcla con hreflang HTML.
2. ⚠️ **`canonical_chain` / `canonical_loop`** — Construir grafo dirigido `URL → canonical_target` y detectar (a) cadenas de longitud >1 sin punto fijo y (b) ciclos. Diferenciar canonical vacío, self-referencing, a URL no crawleada, fuera del scope.
3. ⚠️ **`orphan_url`** (incluyendo variantes `isolated_via_*`) — Cruce de tres fuentes: URLs descubiertas en crawl interno, URLs en sitemaps, URLs vistas en GSC/GA4. Huérfana si está en sitemap/GSC pero NO recibe inlink interno followed.
4. ⚠️ **`content_near_duplicates` / `semantically_similar`** — Comparación N×N. Implementación realista: shingles + MinHash + LSH para near-dup (umbral Jaccard >0.85), y embeddings (SBERT/MiniLM) + ANN (HNSW) para similitud semántica. Peor complejidad de memoria del catálogo.
5. ⚠️ **`internal_redirect_loop` / `redirect_chain`** — Construir grafo `URL → location` siguiendo Location headers, meta refresh, HTTP refresh y JS redirects. Detectar ciclos (Tarjan/Floyd) y cadenas con longitud > umbral. Cruce con grafo de canonicals para detectar combos canonical-then-redirect.

---

## 1. Response Codes / Status (27)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `internal_no_response` | Internal No Response | critical | URL interna que no responde durante el crawl |
| `internal_4xx` | Internal Client Error (4XX) | critical | URL interna devuelve 4XX |
| `internal_5xx` | Internal Server Error (5XX) | critical | URL interna devuelve 5XX |
| `internal_redirect_loop` ⚠️ | Internal Redirect Loop | critical | URL atrapada en bucle de redirecciones |
| `internal_redirect_self` ⚠️ | URL redirects to itself | critical | URL redirige a sí misma |
| `internal_redirect_chain` ⚠️ | Internal Redirect Chain | warning | Cadena de redirecciones (>1 salto) |
| `internal_redirect_broken` ⚠️ | URL redirect broken (4XX/5XX) | critical | Redirección apunta a destino roto |
| `internal_redirect_3xx` | Internal Redirection (3XX) | info | URL devuelve 3XX |
| `redirect_meta_refresh` | Meta Refresh redirect | info | Redirección por meta refresh |
| `redirect_http_refresh` | HTTP Refresh redirect | info | Redirección por header Refresh |
| `redirect_javascript` | JavaScript redirect | info | Redirección por JS |
| `redirect_trailing_slash_mismatch` | Trailing slash mismatch | info | Redirige solo por inconsistencia de slash final |
| `redirect_case_normalization` | Case normalization redirect | info | Redirige solo por normalización mayús/minús |
| `internal_blocked_by_robots` | Blocked by Robots.txt | warning | URL interna bloqueada por robots.txt |
| `internal_blocked_resource` | Internal Blocked Resource | warning | Recurso interno bloqueado |
| `external_blocked_resource` | External Blocked Resource | warning | Recurso externo bloqueado |
| `external_no_response` | External No Response | info | URL externa no responde |
| `external_4xx` | External 4XX | warning | URL externa devuelve 4XX |
| `external_5xx` | External 5XX | warning | URL externa devuelve 5XX |
| `external_redirect_broken` ⚠️ | External redirect broken | warning | Redirección externa apunta a destino roto |
| `external_redirected` | External redirected | info | URL externa con redirección |
| `resource_redirect_broken` ⚠️ | Resource redirect broken | warning | Recurso redirige a destino roto |
| `resource_redirect_self` ⚠️ | Resource redirects to itself | warning | Recurso redirige a sí mismo |
| `resource_redirect_chain_loop` ⚠️ | Resource in chained redirect loop | warning | Recurso en bucle de cadena redirección |
| `resource_redirected` | Redirected resource | info | Recurso de página con redirección |
| `broken_internal_url` ⚠️ | Broken internal URLs | critical | URL interna no funcional |
| `url_resolves_http_and_https` ⚠️ | URL resolves under HTTP and HTTPS | critical | Sin canonicalización entre protocolos |

## 2. Security (14)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `http_url` | HTTP URL | critical | Página servida por HTTP en lugar de HTTPS |
| `mixed_content` | Mixed Content | critical | Página HTTPS carga recursos HTTP |
| `form_url_insecure` | Form URL Insecure | critical | Formulario hace POST a HTTP |
| `form_on_http_url` | Form on HTTP URL | critical | Formulario en página HTTP |
| `http_password_field` | HTTP password input | critical | Campo password sobre HTTP |
| `https_links_to_http` ⚠️ | HTTPS link to HTTP | warning | HTTPS enlaza a página HTTP |
| `protocol_relative_resource` | Protocol-Relative Resource | info | Recursos cargados con `//` |
| `missing_hsts_header` | Missing HSTS Header | info | Falta Strict-Transport-Security |
| `missing_csp_header` | Missing CSP Header | info | Falta Content-Security-Policy |
| `missing_x_content_type_options` | Missing X-Content-Type-Options | info | Falta protección MIME-sniffing |
| `missing_x_frame_options` | Missing X-Frame-Options | info | Falta protección clickjacking |
| `missing_referrer_policy` | Missing Referrer-Policy | info | Falta o inseguro Referrer-Policy |
| `unsafe_cross_origin_links` | Unsafe Cross Origin Links | info | Externos sin rel noopener/noreferrer |
| `bad_content_type` | Bad Content Type | warning | Content-Type incorrecto declarado |

## 3. URL (21)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `url_multiple_slashes` | Multiple Slashes | warning | URL con `//` consecutivos en path |
| `url_contains_space` | Contains A Space | warning | URL con espacios sin codificar |
| `broken_bookmark` | Broken Bookmark | warning | Fragmento `#anchor` no existe en la página |
| `url_non_ascii` | Non ASCII Characters | info | URL con caracteres no ASCII |
| `url_uppercase` | Uppercase | warning | URL con mayúsculas |
| `url_repetitive_path` | Repetitive Path | info | Segmentos de path repetidos |
| `url_internal_search` | Internal Search | info | URL generada por buscador interno |
| `url_has_parameters` | Has Parameters | info | URL con query string |
| `url_ga_tracking_params` | GA Tracking Parameters | info | Parámetros de Google Analytics |
| `url_session_id_params` | Session ID parameters | warning | Session IDs en query string |
| `url_repetitive_query_params` | Repetitive query parameters | info | Parámetros duplicados en query |
| `url_underscores` | Underscores | info | URL con guiones bajos |
| `url_over_115_chars` | Over 115 Characters | info | URL excesivamente larga |
| `url_multiple_gtm` | Multiple GTM codes | warning | Múltiples códigos GTM en página |
| `url_multiple_ga` | Multiple GA codes | warning | Múltiples códigos GA |
| `url_no_gtm` | No GTM code | info | Sin código GTM |
| `url_no_ga` | No GA code | info | Sin código Google Analytics |
| `query_more_than_three_params` | More than 3 query params | info | Más de 3 parámetros en query |
| `query_paginated_params` | Paginated parameters | info | Parámetros de paginación detectados |
| `query_search_filter_params` | Search/filter parameters | info | Parámetros de búsqueda/filtro |
| `query_sort_params` | Sort parameters | info | Parámetros de ordenación |

## 4. Page Titles (10)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `title_missing` | Title Missing/empty | critical | Página sin `<title>` o vacío |
| `title_multiple` | Title Multiple | warning | Más de un `<title>` |
| `title_outside_head` | Title Outside `<head>` | critical | `<title>` fuera de `<head>` |
| `title_duplicate` ⚠️ | Title Duplicate | warning | Mismo título en varias URLs |
| `title_over_60_chars` | Over 60 Characters | info | Título supera 60 caracteres |
| `title_below_30_chars` | Below 30 Characters | info | Título por debajo de 30 caracteres |
| `title_over_561_pixels` | Over 561 Pixels | info | Título supera 561 px de ancho |
| `title_below_200_pixels` | Below 200 Pixels | info | Título por debajo de 200 px |
| `title_same_as_h1` | Same as H1 | info | Título idéntico al H1 |
| `title_same_as_meta_description` | Same as meta description | info | Título y meta description idénticos |

## 5. Meta Description (9)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `meta_desc_missing` | Missing | warning | Sin meta description |
| `meta_desc_empty` | Empty | warning | Etiqueta presente pero vacía |
| `meta_desc_multiple` | Multiple | warning | Múltiples meta descriptions |
| `meta_desc_outside_head` | Outside `<head>` | warning | Meta description fuera de `<head>` |
| `meta_desc_duplicate` ⚠️ | Duplicate | info | Misma meta description en varias URLs |
| `meta_desc_over_155_chars` | Over 155 Characters | info | Excede 155 caracteres |
| `meta_desc_below_70_chars` | Below 70 Characters | info | Por debajo de 70 caracteres |
| `meta_desc_over_985_pixels` | Over 985 Pixels | info | Excede 985 px de ancho |
| `meta_desc_below_400_pixels` | Below 400 Pixels | info | Por debajo de 400 px |

## 6. H1/H2/Headings (14)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `h1_missing` | H1 Missing/empty | warning | Sin H1 (o vacío) |
| `h1_multiple` | H1 Multiple | info | Más de un H1 |
| `h1_alt_text` | Alt Text in h1 | info | H1 contiene alt text de imagen |
| `h1_non_sequential` | H1 Non-sequential | info | Jerarquía salta niveles |
| `h1_duplicate` ⚠️ | H1 Duplicate | info | Mismo H1 en varias URLs |
| `h1_over_70_chars` | H1 Over 70 Characters | info | H1 demasiado largo |
| `h1_below_x_chars` | H1 too short | info | H1 demasiado corto |
| `h2_missing` | H2 Missing | info | Sin H2 |
| `h2_multiple` | H2 Multiple | info | Múltiples H2 |
| `h2_non_sequential` | H2 Non-sequential | info | H2 fuera de secuencia |
| `h2_duplicate` ⚠️ | H2 Duplicate | info | Mismo H2 en varias URLs |
| `h2_over_70_chars` | H2 Over 70 Characters | info | H2 demasiado largo |
| `heading_levels_skip` | Heading levels skip | info | Salto de niveles |
| `heading_empty` | Heading empty | info | Heading sin texto |

## 7. Content (15)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `content_exact_duplicates` ⚠️ | Exact Duplicates | warning | Contenido idéntico en múltiples URLs |
| `content_near_duplicates` ⚠️ | Near Duplicates | info | Contenido sustancialmente similar |
| `content_semantically_similar` ⚠️ | Semantically Similar | info | Páginas con significado comparable |
| `content_technically_duplicate` ⚠️ | Technically duplicate URLs | warning | Duplicados técnicos |
| `content_title_meta_dup` ⚠️ | Duplicate title and meta | warning | Title + meta description duplicados |
| `spelling_errors` | Spelling Errors | info | Errores ortográficos |
| `grammar_errors` | Grammar Errors | info | Errores gramaticales |
| `soft_404` | Soft 404 | warning | 200 OK pero contenido tipo 404 |
| `lorem_ipsum` | Lorem Ipsum Placeholder | warning | Texto placeholder en página live |
| `low_relevance_content` | Low Relevance Content | info | Contenido sin relevancia temática |
| `low_content_pages` | Low Content Pages | info | Páginas con poco contenido |
| `readability_difficult` | Readability Difficult | info | Legibilidad difícil |
| `readability_very_difficult` | Readability Very Difficult | info | Legibilidad muy difícil |
| `html_missing_or_empty` | HTML missing/empty | critical | Sin HTML o vacío |
| `broken_invalid_html` | Broken/invalid HTML | warning | HTML mal formado |

## 8. Images (9)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `image_missing_alt_text` | Missing Alt Text | warning | Imagen sin contenido en alt |
| `image_missing_alt_attribute` | Missing Alt Attribute | warning | Imagen sin atributo alt |
| `image_background_a11y` | Background Images a11y | info | Imagen de fondo posiblemente inaccesible |
| `image_over_100kb` | Over 100 kb | info | Imagen grande (>100KB) |
| `image_alt_over_100_chars` | Alt Text Over 100 Characters | info | Alt text demasiado largo |
| `image_incorrectly_sized` | Incorrectly Sized | info | Imagen no dimensionada para su display |
| `image_missing_size_attrs` | Missing Size Attributes | info | Sin atributos width/height |
| `image_anchored_no_alt` | Image-link no alt | warning | Imagen-link sin alt |
| `image_alt_repeats_text` | Alt repeats adjacent text | info | Alt repetido del texto adyacente |

## 9. Canonicals (31)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `canonical_missing` | Canonical Missing | warning | Página sin canonical |
| `canonical_multiple` | Canonical Multiple | warning | Más de un canonical |
| `canonical_multiple_conflicting` | Multiple Conflicting | critical | Canonicals contradictorios |
| `canonical_outside_head` | Outside `<head>` | critical | Canonical fuera de `<head>` |
| `canonical_relative` | Canonical Is Relative | info | Canonical con URL relativa |
| `canonical_contains_fragment` | Contains Fragment | warning | Canonical con `#fragment` |
| `canonical_invalid_attribute` | Invalid Attribute | warning | Atributos HTML inválidos |
| `canonical_superfluous_attrs` | Superfluous attributes | info | Atributos innecesarios |
| `canonical_malformed_or_empty` | Malformed/empty | warning | Canonical mal formado o vacío |
| `canonicalised` | Canonicalised | info | Página canonicaliza a otra URL |
| `canonical_to_non_indexable` ⚠️ | Non-Indexable Canonical | warning | Canonical apunta a no indexable |
| `canonical_unlinked` ⚠️ | Unlinked | warning | Canonical apunta a URL no enlazada |
| `canonical_to_4xx` ⚠️ | Canonical points to 404 | critical | Canonical a 404 |
| `canonical_to_5xx` ⚠️ | Canonical points to 5XX | critical | Canonical a 5XX |
| `canonical_to_3xx` ⚠️ | Canonical to redirecting | warning | Canonical a URL que redirige |
| `canonical_to_disallowed` ⚠️ | Canonical to disallowed | warning | Canonical a bloqueada por robots |
| `canonical_to_noindex` ⚠️ | Canonical to noindex | warning | Canonical a noindex |
| `canonical_chain` ⚠️ | Canonical chain | warning | Cadena de canonicals |
| `canonical_loop` ⚠️ | Canonical loop | critical | Canonicals circulares |
| `canonical_to_homepage` | Canonical to homepage | warning | Canonical apunta a homepage |
| `canonical_to_http` | Canonical to HTTP | critical | Canonical apunta a HTTP |
| `canonical_to_https` | Canonical to HTTPS | info | Canonical apunta a HTTPS |
| `canonical_html_vs_header_mismatch` | HTML vs header mismatch | warning | Canonical HTML difiere del de cabecera |
| `canonical_html_and_header` | In HTML and header | info | Declarado en ambos |
| `canonical_only_in_rendered_dom` | Only in rendered DOM | warning | Solo aparece tras render JS |
| `canonical_render_vs_source_mismatch` | Render vs source mismatch | warning | Cambia tras renderizado JS |
| `canonical_to_external` | To external URL | info | Canonical apunta a externo |
| `canonical_to_different_internal` | To different internal | info | Canonical a otra URL interna |
| `base_url_malformed` | Base URL malformed | info | `<base>` mal formada |
| `base_url_multiple_mismatched` | Multiple mismatched bases | info | Múltiples `<base>` contradictorios |
| `base_url_multiple` | Multiple base URLs | info | Múltiples `<base>` |

## 10. Pagination (7)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `pagination_not_in_anchor` | Not In Anchor Tag | warning | URL paginada no en `<a>` |
| `pagination_non_200` ⚠️ | Non-200 Pagination | warning | URL paginada con código no-200 |
| `pagination_unlinked` ⚠️ | Unlinked Pagination | warning | URL paginada sin inlinks |
| `pagination_multiple` | Multiple Pagination URLs | info | Múltiples secuencias |
| `pagination_loop` ⚠️ | Pagination Loop | warning | Secuencia paginada circular |
| `pagination_sequence_error` ⚠️ | Sequence Error | warning | Error de orden lógico |
| `pagination_non_indexable` | Pagination Non-Indexable | warning | Paginación marcada noindex |

## 11. Directives (28)

| id | Nombre | Severidad | Descripción |
|---|---|---|---|
| `robots_meta_outside_head` | Outside `<head>` | critical | Meta robots fuera de `<head>` |
| `noindex` | Noindex | info | Directiva noindex aplicada |
| `nofollow` | Nofollow | info | Directiva nofollow aplicada |
| `none` | None | info | Directiva none |
| `noindex_nofollow_combined` | noindex+nofollow | info | Combinación noindex+nofollow |
| `unavailable_after` | Unavailable_After | info | Directiva presente |
| `nosnippet` | NoSnippet | info | Directiva nosnippet |
| `noimageindex` | NoImageIndex | warning | Directiva noimageindex |
| `noodp` | NoODP | info | Directiva legacy |
| `noydir` | NoYDIR | info | Directiva legacy |
| `notranslate` | NoTranslate | info | Directiva notranslate |
| `noindex_html_vs_header_mismatch` | HTML vs header mismatch | warning | Noindex contradictorio |
| `nofollow_html_vs_header_mismatch` | HTML vs header mismatch | warning | Nofollow contradictorio |
| `noindex_html_and_header` | In HTML and header | info | Declarado en ambos |
| `nofollow_html_and_header` | In HTML and header | info | Declarado en ambos |
| `noindex_multiple` | Multiple noindex | info | Múltiples noindex |
| `nofollow_multiple` | Multiple nofollow | info | Múltiples nofollow |
| `noindex_only_in_source` | Only in original HTML | warning | Solo en HTML inicial |
| `nofollow_only_in_source` | Only in original HTML | warning | Solo en HTML inicial |
| `disallowed_js_file` | Disallowed JS file | critical | JS bloqueado por robots |
| `disallowed_image` | Disallowed image | critical | Imagen bloqueada |
| `disallowed_css` | Disallowed CSS | critical | CSS bloqueado |
| `internal_disallowed_links` ⚠️ | Internal Disallowed URLs | info | URLs enlazadas pero bloqueadas |
| `head_noscript_with_image` | `<noscript>` with image in head | critical | `<noscript>` con imagen en `<head>` |
| `head_invalid_html` | Invalid HTML in head | critical | Elementos inválidos en `<head>` |
| `head_noscript_tag` | `<noscript>` in head | info | `<noscript>` en `<head>` |
| `html_over_2mb` | HTML > 2MB | critical | HTML supera 2MB (límite Google) |
| `form_with_get_method` | Form GET method | warning | Formulario con método GET |

## 12. Hreflang / International (33)

Reglas detalladas en informe del agente investigador. Categoría más compleja del catálogo (todas requieren cluster validation cross-URL).

## 13. JavaScript / Rendered (15)

Detección de discrepancias HTML inicial vs rendered (canonical, title, h1, meta description, links). Requiere `requires_rendered_html=True`.

## 14. Links / Internal Linking (19)

Análisis del grafo de links: outlinks rotos, anchor text, profundidad, equity restringida, balance follow/nofollow.

## 15. Orphan / Isolated Pages (5)

Cruce sitemap + GSC + crawl interno. Todas ⚠️ cross-URL.

## 16. Sitemaps XML (12)

Validación de sitemap.xml: tamaños, URLs no-200, URLs canonicalizadas en sitemap, mismatch crawl vs sitemap.

## 17. Structured Data (6)

| id | Nombre | Severidad |
|---|---|---|
| `schema_validation_errors` | Validation Errors | warning |
| `schema_rich_result_errors` | Rich Result Validation Errors | warning |
| `schema_parse_errors` | Parse Errors | warning |
| `schema_missing` | Missing | info |
| `schema_validation_warnings` | Validation Warnings | info |
| `schema_rich_result_warnings` | Rich Result Warnings | info |

## 18. PageSpeed / Performance (28)

Lighthouse-equivalent: render-blocking, cache lifetimes, layout shifts, image delivery, legacy JS, unused CSS, etc.

## 19. Mobile (15)

Viewport, font size, tap targets, plugins no soportados, content sized to viewport.

## 20. AMP (30)

Validación AMP: response codes, canonical AMP↔non-AMP, validación de tags AMP, `<html amp>`, boilerplate.

## 21. Search Traffic (11)

Cruce GSC/GA4: URLs canonicalizadas/disallowed/noindex/redirect/4xx/orphan que reciben tráfico orgánico.

## 22. Accessibility (~95)

Reglas axe-core/WCAG: ARIA, contraste, lang, landmarks, formularios, tablas, frames, focus, skip-links.

---

## Implementation notes

- **Two-phase pipeline is mandatory**: rules marked ⚠️ cannot be evaluated
  during streaming. Phase 1: crawl + persist URLs and their relations to
  DuckDB. Phase 2: rule engine runs all modules over the complete graph.
- **Primary sources** for the technical SEO standards encoded by these rules:
  - [Google Search Central](https://developers.google.com/search) — indexing,
    canonicals, hreflang, structured data appearance, sitemaps
  - [MDN Web Docs](https://developer.mozilla.org/en-US/docs/Web) — HTTP
    semantics, HTML element semantics, security headers
  - [web.dev](https://web.dev/) — Core Web Vitals, performance, mobile
  - [Schema.org](https://schema.org/) — structured data vocabulary
  - [W3C / WAI](https://www.w3.org/WAI/) — accessibility (WCAG)
  - [sitemaps.org](https://www.sitemaps.org/) — XML sitemap protocol
