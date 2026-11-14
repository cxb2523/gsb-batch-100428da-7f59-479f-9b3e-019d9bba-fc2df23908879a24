# Changelog

All notable changes to **webxray** are documented here.

---

## [1.4.0] – 2026-11-14

### Added
- `discover_urls()` devuelve ahora un registro por cada URL candidata con su final trazable, en vez de una lista plana que silenciaba saltos y fallos. Outcomes estables: `fetched`, `parse_failed`, `depth_limit`, `request_failed` (con resumen del error en el campo `error`), `excluded_cross_site` y `duplicate`. El esquema del registro es fijo: `url`, `outcome`, `depth`, `source`, `status`, `error`, `links_found`.
- `scannable_urls()`: extrae del registro las URLs que el escaneo debe cubrir (todas las que se encolaron, en el mismo orden BFS que antes). El flujo principal la usa, asi que XSS/SQLi/cabeceras siguen escaneando las mismas URLs y una pagina fallida no hace perder candidatas.
- `--crawl-report`: escribe el registro de descubrimiento en un documento JSON/JSONL separado (respeta `--format`). No se mezcla con los findings, cuya estructura queda intacta.
- `crawl_outcome_summary()` y linea de log con el conteo de outcomes al terminar el descubrimiento.
- Tests contra servidor HTTP local cubriendo fetch normal, fallo de peticion (sin interrumpir la ronda), respuesta no parseable, truncado por profundidad, exclusion cross-site, duplicados, estabilidad del esquema y compatibilidad del CLI.

### Changed
- Un fallo de red o de parseo en una pagina ya no se omite en silencio: se registra (`request_failed`/`parse_failed`) y el crawler continua con el resto de la cola.
- `discover_urls()` devuelve `List[dict]` en vez de `List[str]`; los consumidores que necesiten la lista antigua usan `scannable_urls()`.

### Notas de compatibilidad
- Las reglas de mismo host, desduplicacion, semantica de profundidad y la aplicacion de `-H`/`-c`/`-x`/`-k`/`--rate` a todas las peticiones no cambian.
- `--json-output`/`--stdout` siguen emitiendo unicamente findings normalizados, con el mismo esquema.

---

## [1.3.0] – 2026-11-05

### Added
- Opciones para bug bounty, aplicadas a todas las peticiones (crawler, XSS, SQLi, cabeceras y `--waf-xss`): `-H/--header` (repetible), `-c/--cookie`, `-x/--proxy` (Burp), `-k/--insecure` y `--rate` (máximo de peticiones por segundo, compartido entre sesiones).
- Tests (`pytest`) contra un servidor HTTP local: llegada de cabeceras y cookies, límite de peticiones, `-k` y detección de XSS escapado/sin escapar. La CI los ejecuta.

### Changed
- Las sesiones HTTP se crean en un único punto (`make_session()`), en vez de en cuatro sitios distintos.
- README: el ejemplo de pipeline ya no depende de una herramienta privada.

---

## [1.2.0] – 2026-08-23

### Fixed
- `check_xss()`: eliminado el falso positivo estructural de "if payload in r.text". Ahora usa un marcador (`wxr` + 6 chars aleatorios) unico por request embebido en el payload, y clasifica el reflejo en `unescaped` (explotable), `escaped` (HTML-entity-encoded, no explotable) o `unclear`. Los findings de tipo `escaped`/`unclear` bajan de severidad y llevan nota explicita en vez de tratarse igual que un reflejo real.
- `_sqli_hit()`: `status_changed` y `size_changed` dejan de ser señales suficientes por separado (podian disparar en cualquier pagina con contenido semi-dinamico: fecha, contador, sesion, ads). Ahora `error_kw` sigue bastando por si sola (senal fuerte); `status_changed` y `size_changed` solo cuentan si ocurren **ambas a la vez**, y en ese caso el finding se marca con confianza/severidad inferior a un hit por `error_kw`.

### Changed
- Umbral de `size_changed` en SQLi: de 200 bytes fijos a proporcional al tamano de la respuesta baseline (`max(100, 15% del baseline)`), para no disparar en respuestas pequeñas ni ser demasiado laxo en respuestas grandes.
- Findings de XSS y SQLi ahora incluyen los campos `confidence`, `severity` y `note` para reflejar el nivel de certeza de la deteccion.

### Added
- Campo `"context"` en los findings de XSS sin escapar: heuristica que estima si el reflejo cae en `script-tag`, `unquoted-attribute`, `plain-text` o `unknown`, mirando los caracteres inmediatamente antes de la coincidencia en el body.

---

## [1.1.0] – 2026-03-24

### Added
- Banner ASCII en arranque.
- `__version__ = "1.1.0"` y flag `--version`.
- SQLi en formularios POST (`sqli_post`).
- Payloads XSS ampliados: `<svg/onload=alert(1)>`, `autofocus onfocus`.
- Keywords SQLi adicionales: `pg::`, `pdo`, `syntax error`, `unclosed quotation`.
- Cabecera `Permissions-Policy` en lista de comprobacion.
- Type hints con `typing` (compatible Python 3.8+).

### Changed
- Archivo renombrado: `webflow.py` → `webxray.py`.
- Docstrings y User-Agent actualizados a `webxray`.
- `str | None` → `Optional[str]` para compatibilidad Python 3.8/3.9.
- `_sqli_hit()` extrae logica de deteccion SQLi como funcion reutilizable.
- Umbral de cambio de tamano en SQLi: 100 → 200 bytes (menos falsos positivos).

### Removed
- `webflow.py` (reemplazado por `webxray.py`).

---

## [1.0.0] – 2023-12-04

### Added
- Version inicial: crawling, XSS GET, SQLi GET, cabeceras, modo `--waf-xss`.
