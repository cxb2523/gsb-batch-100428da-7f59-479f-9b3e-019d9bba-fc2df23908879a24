"""Acceptance tests for the async crawler against a local self-made site.

The site fixture serves:
- `/`                index linking to everything below (and to /final directly)
- `/old` → `/old-2` → `/final`   302 redirect chain
- `/final`           landing page with title/canonical (linked directly too,
                     so we can assert it is stored exactly once)
- `/private/secret`  disallowed by robots.txt
- `/slow`            sleeps longer than the client timeout → request error
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from crawlforge.engine.crawler import CrawlConfig
from crawlforge.engine.runner import run_crawl


def _html(title: str, body: str = "", head_extra: str = "") -> bytes:
    return (
        f"<!DOCTYPE html><html><head><title>{title}</title>{head_extra}</head>"
        f"<body>{body}</body></html>"
    ).encode()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep test output quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str = "text/html") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /private\n", "text/plain")
        elif path == "/":
            self._send(
                200,
                _html(
                    "Home",
                    '<a href="/old">old</a>'
                    '<a href="/final">final direct</a>'
                    '<a href="/private/secret">secret</a>'
                    '<a href="/slow">slow</a>',
                ),
            )
        elif path == "/old":
            self._redirect("/old-2")
        elif path == "/old-2":
            self._redirect("/final")
        elif path == "/final":
            self._send(
                200,
                _html(
                    "Final Landing",
                    "<h1>Landed</h1>",
                    head_extra='<link rel="canonical" href="/final">',
                ),
            )
        elif path == "/private/secret":
            self._send(200, _html("Secret", "<h1>should never be fetched</h1>"))
        elif path == "/slow":
            time.sleep(5)  # longer than the client timeout in the tests
            self._send(200, _html("Slow"))
        else:
            self._send(404, _html("not found"))


@pytest.fixture()
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _config(base: str, **overrides: object) -> CrawlConfig:
    kwargs = {
        "start_url": base + "/",
        "max_urls": 50,
        "max_depth": 5,
        "concurrency": 4,
        "timeout_s": 0.5,
    }
    kwargs.update(overrides)
    return CrawlConfig(**kwargs)  # type: ignore[arg-type]


async def test_redirect_block_and_error_outcomes(site: str) -> None:
    con, summary = await run_crawl(_config(site))

    rows = con.execute(
        "SELECT url, final_url, status_code, redirect_count, from_robots,"
        "       is_indexable, indexability_reason, title, canonical"
        " FROM urls"
    ).fetchall()
    by_url = {r[0]: r for r in rows}

    # Every queued URL left exactly one auditable row.
    assert len(rows) == len(by_url), "duplicate URL rows in urls table"

    final_url = f"{site}/final"

    # Redirect target got its own row with content signals filled in.
    assert final_url in by_url
    final = by_url[final_url]
    assert final[2] == 200
    assert final[7] == "Final Landing"
    assert final[8] == final_url

    # Original redirect URL kept its row, final_url points at the landing page.
    old = by_url[f"{site}/old"]
    assert old[1] == final_url
    assert old[2] == 200
    assert old[3] == 2  # two 302 hops

    # The intermediate hop was followed, not stored as a page.
    assert f"{site}/old-2" not in by_url

    # robots.txt-disallowed path: blocked marker, not a network error.
    blocked = by_url[f"{site}/private/secret"]
    assert blocked[2] == 0
    assert blocked[4] is True  # from_robots
    assert blocked[5] is False  # is_indexable
    assert blocked[6] == "blocked by robots.txt"

    # Blocked rows produce no link discovery.
    n_links = con.execute(
        "SELECT COUNT(*) FROM links l JOIN urls u ON l.source_url_id = u.url_id"
        " WHERE u.from_robots"
    ).fetchone()[0]
    assert n_links == 0

    # Timing-out address: request failure, clearly distinct from the block.
    slow = by_url[f"{site}/slow"]
    assert slow[2] == 0
    assert slow[4] is False  # from_robots
    assert slow[5] is False  # is_indexable
    assert slow[6].startswith("request failed:")

    # Both count as failures, with different reasons.
    assert summary["urls_blocked_by_robots"] == 1
    assert summary["urls_request_errors"] == 1
    assert summary["urls_failed"] == 2

    meta = dict(con.execute("SELECT key, value FROM crawl_meta").fetchall())
    assert meta["urls_blocked_by_robots"] == "1"
    assert meta["urls_request_errors"] == "1"
    assert meta["urls_failed"] == "2"


async def test_max_urls_limit_respected(site: str) -> None:
    con, summary = await run_crawl(_config(site, max_urls=3))
    n = con.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
    assert n <= 3
    assert summary["urls_total"] <= 3
