"""Tests del flujo de descubrimiento con outcomes trazables.

Cubre, contra un servidor HTTP local:
- fetch normal y parseo (outcome=fetched, con status/links_found/source/depth)
- fallo de peticion que NO interrumpe la ronda (outcome=request_failed)
- respuesta no parseable (outcome=parse_failed)
- truncado por profundidad (outcome=depth_limit, sin peticion)
- enlaces cross-site excluidos (outcome=excluded_cross_site)
- enlaces repetidos ignorados (outcome=duplicate)
- estabilidad del esquema de registros y compatibilidad del flujo principal
"""

import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import webxray  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def reset_http_config():
    yield
    webxray.SCAN_HEADERS.clear()
    webxray.SCAN_PROXIES = None
    webxray.ScanSession.verify_tls = True
    webxray.ScanSession.min_interval = 0.0
    webxray.ScanSession._last_request = 0.0


def _start_server(routes, crash_paths=()):
    """Levanta un servidor local; routes: dict path -> (status, body_bytes,
    extra_headers). Las rutas ausentes responden 404. Las rutas en
    crash_paths cortan la conexion sin responder (fallo de red a nivel cliente)."""
    crash_paths = set(crash_paths)
    requested = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(urlparse(self.path).path)
            path = urlparse(self.path).path
            if path in crash_paths:
                # Corta sin respuesta: el cliente ve ConnectionError, y la
                # ruta sigue siendo del mismo host que el resto del sitio.
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            status, body, extra = routes.get(path, (404, b"not found", {}))
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", requested
    finally:
        srv.shutdown()


@pytest.fixture()
def crawl_server():
    routes = {
        "/": (
            200,
            b'<a href="/a">a</a>'
            b'<a href="/a">a-repetido</a>'
            b'<a href="/b">b</a>'
            b'<a href="https://external.example.org/x">externo</a>',
            {},
        ),
        "/a": (200, b'<a href="/b">b-otra-vez</a>', {}),
        "/b": (200, b"<p>pagina b</p>", {}),
    }
    yield from _start_server(routes)


@pytest.fixture()
def failure_server():
    routes = {
        "/": (
            200,
            b'<a href="/ok">ok</a><a href="/empty">vacio</a><a href="/crash">caida</a>',
            {},
        ),
        "/ok": (200, b"<p>todo bien</p>", {}),
        # Cuerpo vacio: lxml no puede parsearlo -> parse_failed
        "/empty": (200, b"", {}),
    }
    # /crash cierra la conexion sin responder -> request_failed
    yield from _start_server(routes, crash_paths=("/crash",))


def _by_url(records):
    return {r["url"]: r for r in records}


def test_fetched_pages_carry_full_record(crawl_server):
    base, requested = crawl_server
    records = webxray.discover_urls(base, max_depth=2, timeout=5)
    index = _by_url(records)

    seed = index[base]
    assert seed["outcome"] == webxray.OUTCOME_FETCHED
    assert seed["depth"] == 0
    assert seed["source"] is None
    assert seed["status"] == 200
    assert seed["error"] is None
    assert seed["links_found"] == 4

    page_a = index[base + "/a"]
    assert page_a["outcome"] == webxray.OUTCOME_FETCHED
    assert page_a["depth"] == 1
    assert page_a["source"] == base
    assert page_a["links_found"] == 1

    assert requested[:1] == ["/"]  # la semilla se pidio primero


def test_cross_site_links_are_excluded_and_not_scanned(crawl_server):
    base, _ = crawl_server
    records = webxray.discover_urls(base, max_depth=2, timeout=5)
    external = [r for r in records if r["outcome"] == webxray.OUTCOME_CROSS_SITE]
    assert len(external) == 1
    rec = external[0]
    assert rec["url"] == "https://external.example.org/x"
    assert rec["source"] == base
    assert rec["status"] is None
    # Nunca entra en la lista que se lleva el escaneo XSS/SQLi/headers.
    assert "https://external.example.org/x" not in webxray.scannable_urls(records)


def test_duplicate_links_are_recorded_once_per_occurrence(crawl_server):
    base, requested = crawl_server
    records = webxray.discover_urls(base, max_depth=2, timeout=5)
    dupes = [r for r in records if r["outcome"] == webxray.OUTCOME_DUPLICATE]
    # /a aparece dos veces en "/" y /b se enlaza desde "/" y desde "/a":
    # las ocurrencias posteriores quedan registradas como duplicado.
    dup_urls = {r["url"] for r in dupes}
    assert base + "/a" in dup_urls
    assert base + "/b" in dup_urls
    assert all(r["source"] is not None for r in dupes)

    # El escaneo ve cada URL una sola vez, en orden BFS.
    urls = webxray.scannable_urls(records)
    assert urls == [base, base + "/a", base + "/b"]
    assert len(urls) == len(set(urls))


def test_depth_limit_is_recorded_and_not_requested(crawl_server):
    base, requested = crawl_server
    records = webxray.discover_urls(base, max_depth=1, timeout=5)
    index = _by_url(records)

    assert index[base]["outcome"] == webxray.OUTCOME_FETCHED
    for path in ("/a", "/b"):
        rec = index[base + path]
        assert rec["outcome"] == webxray.OUTCOME_DEPTH_LIMIT
        assert rec["depth"] == 1
        assert rec["status"] is None
        assert rec["error"] is None

    # depth >= max_depth no genera peticiones: solo se pidio la semilla.
    assert requested == ["/"]
    # Aun asi, las URLs truncadas siguen siendo candidatas a escaneo,
    # igual que en la version antigua.
    assert webxray.scannable_urls(records) == [base, base + "/a", base + "/b"]


def test_request_failure_does_not_interrupt_the_round(failure_server):
    base, requested = failure_server
    records = webxray.discover_urls(base, max_depth=2, timeout=5)
    index = _by_url(records)

    failed = index[base + "/crash"]
    assert failed["outcome"] == webxray.OUTCOME_REQUEST_FAILED
    assert failed["depth"] == 1
    assert failed["source"] == base
    assert failed["status"] is None
    assert failed["error"]  # resumen del error, no vacio
    assert "Error" in failed["error"]

    # El resto de la ronda continua con normalidad, pese al fallo en /crash.
    survivor = index[base + "/ok"]
    assert survivor["outcome"] == webxray.OUTCOME_FETCHED
    assert survivor["status"] == 200
    assert "/crash" in requested  # se intento, no se omitio en silencio
    # La URL fallida se mantiene como candidata: el escaneo no la pierde.
    assert base + "/crash" in webxray.scannable_urls(records)


def test_parse_failure_is_distinct_from_request_failure(failure_server):
    base, _ = failure_server
    records = webxray.discover_urls(base, max_depth=2, timeout=5)
    index = _by_url(records)

    empty = index[base + "/empty"]
    assert empty["outcome"] == webxray.OUTCOME_PARSE_FAILED
    assert empty["status"] == 200  # hubo respuesta HTTP...
    assert empty["error"]          # ...pero no se pudo parsear
    assert empty["links_found"] == 0

    ok = index[base + "/ok"]
    assert ok["outcome"] == webxray.OUTCOME_FETCHED
    assert ok["error"] is None


def test_records_have_stable_schema_and_serialize(crawl_server):
    base, _ = crawl_server
    records = webxray.discover_urls(base, max_depth=2, timeout=5)
    assert records  # no vacio
    for rec in records:
        # Las siete claves estables, siempre presentes.
        assert set(rec.keys()) == set(webxray.CRAWL_RECORD_FIELDS)
        assert isinstance(rec["links_found"], int)
        assert rec["status"] is None or isinstance(rec["status"], int)
    # Serializable a JSON y JSONL sin sobresaltos.
    json.dumps(records)
    for line in webxray.serialize_findings(records, "jsonl").splitlines():
        assert set(json.loads(line).keys()) == set(webxray.CRAWL_RECORD_FIELDS)


def test_outcome_summary_counts_every_outcome(crawl_server):
    base, _ = crawl_server
    records = webxray.discover_urls(base, max_depth=2, timeout=5)
    summary = webxray.crawl_outcome_summary(records)
    assert summary[webxray.OUTCOME_FETCHED] >= 3
    assert summary[webxray.OUTCOME_DUPLICATE] >= 1
    assert summary[webxray.OUTCOME_CROSS_SITE] == 1
    assert sum(summary.values()) == len(records)


def test_scan_pipeline_still_runs_on_scannable_urls(crawl_server):
    base, _ = crawl_server
    records = webxray.discover_urls(base, max_depth=2, timeout=5)
    urls = webxray.scannable_urls(records)
    session = webxray.make_session()
    # Las tres fases del escaneo por defecto siguen funcionando sobre la
    # lista de URLs extraida del nuevo registro.
    for u in urls:
        assert isinstance(webxray.check_xss(session, u, timeout=5), list)
        assert isinstance(webxray.check_headers(session, u, timeout=5), list)
    # Sin params, SQLi solo hace el baseline + formularios: no debe romperse.
    assert isinstance(webxray.check_sqli(session, urls[0], timeout=5), list)


def test_cli_keeps_findings_and_writes_separate_crawl_report(crawl_server, tmp_path):
    base, _ = crawl_server
    report = tmp_path / "crawl.jsonl"
    proc = subprocess.run(
        [
            sys.executable, "-I", str(REPO_ROOT / "webxray.py"),
            "-u", base, "-d", "2", "--no-sqli",
            "--stdout", "--format", "jsonl",
            "--crawl-report", str(report),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    findings = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    # El stdout sigue siendo SOLO findings: ninguna linea tiene el schema crawl.
    for finding in findings:
        assert "outcome" not in finding
        for key in ("type", "vector", "target", "host", "severity", "raw"):
            assert key in finding

    report_records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert report_records
    assert all(set(r.keys()) == set(webxray.CRAWL_RECORD_FIELDS) for r in report_records)
    assert any(r["outcome"] == webxray.OUTCOME_FETCHED for r in report_records)
    assert any(r["outcome"] == webxray.OUTCOME_CROSS_SITE for r in report_records)


def test_http_options_still_apply_to_crawler_requests(failure_server):
    base, requested = failure_server
    webxray.configure_http(["X-Trace: wx"], "sid=1", None, False, 0)
    captured = {}

    original_request = webxray.ScanSession.request

    def spy_request(self, method, url, *args, **kwargs):
        captured.setdefault("calls", []).append((url, kwargs.get("verify"), dict(self.headers)))
        return original_request(self, method, url, *args, **kwargs)

    webxray.ScanSession.request = spy_request
    try:
        webxray.discover_urls(base, max_depth=2, timeout=5)
    finally:
        webxray.ScanSession.request = original_request

    assert captured["calls"]
    for _url, verify, headers in captured["calls"]:
        assert verify is True
        assert headers.get("X-Trace") == "wx"
        assert headers.get("Cookie") == "sid=1"
