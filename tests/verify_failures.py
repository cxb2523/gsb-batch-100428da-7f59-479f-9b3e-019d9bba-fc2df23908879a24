#!/usr/bin/env python3
"""
Verification harness for the per-page failure manifest feature.

It spins up a small local static site that deliberately contains:
  * a page referencing a non-existent image (asset-level HTTP 404)
  * a link to a page that returns 404 (whole-page failure)
  * a reachable image and stylesheet (success path, rewritten to relative paths)
  * an image pointing at a closed local port (connection failure on an asset)

Then it runs the crawler once single-threaded and once with --threads 4,
and asserts that:
  * the pages that could be fetched are still saved to disk
  * the 404 page is NOT saved
  * the failure manifest lists every failed resource and failed page,
    grouped by the owning page with accurate counts
  * successful resources are rewritten to relative paths
  * the manifest content is identical between the single- and multi-threaded
    runs (correct ownership, no double counting, no cross-page attribution)
  * a fully successful run produces no manifest at all
"""

import contextlib
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRAWLER = os.path.join(REPO_ROOT, "sourceDownloader.py")

# A 1x1 transparent PNG, so the "good" image is a real binary file.
GOOD_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfab4400"
    "00000049454e44ae426082"
)

INDEX_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Index</title>
  <link rel="stylesheet" href="/assets/style.css">
</head>
<body>
  <h1>Index page</h1>
  <img src="/assets/good.png" alt="good">
  <img src="/assets/missing.png" alt="missing">
  <img src="http://127.0.0.1:{closed_port}/down.png" alt="dead origin">
  <a href="/about.html">about (exists)</a>
  <a href="/does-not-exist.html">broken page (404)</a>
</body>
</html>
"""

ABOUT_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>About</title></head>
<body>
  <h1>About page</h1>
  <img src="/assets/also-missing.jpg" alt="missing too">
  <a href="/">home</a>
</body>
</html>
"""

STYLE_CSS = "body { font-family: sans-serif; }\n"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_site(site_dir: str, closed_port: int) -> None:
    os.makedirs(os.path.join(site_dir, "assets"), exist_ok=True)
    with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_HTML.format(closed_port=closed_port))
    with open(os.path.join(site_dir, "about.html"), "w", encoding="utf-8") as f:
        f.write(ABOUT_HTML)
    with open(os.path.join(site_dir, "assets", "style.css"), "w", encoding="utf-8") as f:
        f.write(STYLE_CSS)
    with open(os.path.join(site_dir, "assets", "good.png"), "wb") as f:
        f.write(GOOD_PNG)


def start_server(site_dir: str) -> tuple[str, socket.socket]:
    port = _free_port()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(16)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    def serve():
        handler = QuietHandler
        # Serve files relative to site_dir regardless of the harness CWD.
        cwd = os.getcwd()
        os.chdir(site_dir)
        try:
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                try:
                    handler(conn, ("127.0.0.1", port), server)
                except Exception:
                    pass
        finally:
            os.chdir(cwd)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}/", server


def run_crawler(base_url: str, work_dir: str, threads: int, depth: str = "1") -> tuple[str, str]:
    """Run the crawler as a subprocess (real --threads CLI) and capture output."""
    proc = subprocess.run(
        [
            sys.executable, "-I", CRAWLER, base_url,
            "--depth", depth,
            "--threads", str(threads),
        ],
        cwd=work_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Crawler exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc.stdout, proc.stderr


def project_dir_of(work_dir: str, base_url: str) -> str:
    from urllib.parse import urlparse
    netloc = urlparse(base_url).netloc
    safe = netloc.replace(".", "_")
    for illegal_char in ':?*<>|"':
        safe = safe.replace(illegal_char, "_")
    candidate = os.path.join(work_dir, safe)
    if os.path.isdir(candidate):
        return candidate
    raise AssertionError(f"Output directory not found: {candidate}")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  [ok] {message}")


def verify_run(work_dir: str, base_url: str, threads: int) -> str:
    print(f"\n=== Run with --threads {threads} ===")
    stdout, _ = run_crawler(base_url, work_dir, threads)
    project = project_dir_of(work_dir, base_url)

    # 1. Reachable pages are saved; the 404 page is not.
    check(os.path.isfile(os.path.join(project, "index.html")),
          "reachable index page was saved")
    check(os.path.isfile(os.path.join(project, "about.html")),
          "reachable about page was saved")
    check(not os.path.exists(os.path.join(project, "does-not-exist.html")),
          "whole-page 404 was NOT written to disk")

    # 2. Successful assets saved; missing asset not saved.
    check(os.path.isfile(os.path.join(project, "assets", "good.png")),
          "successful image saved to disk")
    check(os.path.isfile(os.path.join(project, "assets", "style.css")),
          "successful stylesheet saved to disk")
    check(not os.path.exists(os.path.join(project, "assets", "missing.png")),
          "missing image was not written to disk")

    # 3. Success path still rewritten to relative paths.
    with open(os.path.join(project, "index.html"), encoding="utf-8") as f:
        saved_index = f.read()
    check('src="assets/good.png"' in saved_index,
          "successful image rewritten to relative path")
    check('href="assets/style.css"' in saved_index,
          "successful stylesheet rewritten to relative path")
    check("/assets/missing.png" in saved_index,
          "failed image keeps its original (unrewritten) reference")

    # 4. Manifest exists and contains every expected failure.
    manifest_path = os.path.join(project, "failures.txt")
    check(os.path.isfile(manifest_path), "failure manifest generated")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = f.read()

    index_url = base_url
    about_url = urljoin_safe(base_url, "/about.html")
    missing_png = urljoin_safe(base_url, "/assets/missing.png")
    missing_jpg = urljoin_safe(base_url, "/assets/also-missing.jpg")
    broken_page = urljoin_safe(base_url, "/does-not-exist.html")

    check(f"Page: {index_url}" in manifest, "manifest groups failures under index page")
    check(f"Page: {about_url}" in manifest, "manifest groups failures under about page")
    check(missing_png in manifest and "http-error" in manifest and "HTTP 404" in manifest,
          "missing image recorded with original URL and HTTP 404 classification")
    check(broken_page in manifest, "404 link recorded as a whole-page failure")
    check(missing_jpg in manifest, "about page's missing image attributed to about page")
    check("connection-error" in manifest, "connection failure classified as connection-error")
    check("Total failed resources: 3" in manifest, "exactly 3 failed resources counted")
    check("Total failed pages: 1" in manifest, "exactly 1 failed page counted")
    check("Failed resources: 2 | Failed page entries: 0" in manifest,
          "index page per-group counts are correct")
    check("Failed resources: 1 | Failed page entries: 0" in manifest,
          "about page per-group counts are correct")
    check("Failed resources: 0 | Failed page entries: 1" in manifest,
          "404 page group records the whole-page failure")

    # 5. Terminal output contains the exact same manifest content.
    check(manifest.strip() in stdout, "terminal output matches the manifest content")

    return manifest


def urljoin_safe(base: str, path: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, path)


def verify_all_success(work_dir: str, base_url: str) -> None:
    print("\n=== Fully successful run produces no manifest ===")
    good_root = os.path.join(work_dir, "good_site")
    os.makedirs(good_root, exist_ok=True)
    with open(os.path.join(good_root, "index.html"), "w", encoding="utf-8") as f:
        f.write(
            '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
            '<body><img src="/assets/good.png"></body></html>'
        )
    os.makedirs(os.path.join(good_root, "assets"), exist_ok=True)
    with open(os.path.join(good_root, "assets", "good.png"), "wb") as f:
        f.write(GOOD_PNG)

    good_url, good_server = start_server(good_root)
    try:
        good_work = os.path.join(work_dir, "good_run")
        os.makedirs(good_work, exist_ok=True)
        stdout, _ = run_crawler(good_url, good_work, 1)
        project = project_dir_of(good_work, good_url)
        check(not os.path.exists(os.path.join(project, "failures.txt")),
              "no manifest written when every object succeeds")
        check("Failure report" not in stdout,
              "no failure report printed when every object succeeds")
    finally:
        good_server.close()


def verify_timeout_inprocess() -> None:
    print("\n=== Timeout classification (in-process, patched slow server) ===")
    sys.path.insert(0, REPO_ROOT)
    import importlib
    crawler = importlib.import_module("sourceDownloader")

    from http.server import BaseHTTPRequestHandler, HTTPServer

    index_html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="/slow.css"></head>'
        '<body>ok</body></html>'
    )

    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/slow.css":
                time.sleep(2.0)  # hangs past the crawler's patched timeout
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(index_html.encode("utf-8"))

        def log_message(self, *args):
            pass

    original_timeout = crawler.TIMEOUT
    crawler.TIMEOUT = 0.3
    httpd = HTTPServer(("127.0.0.1", 0), SlowHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    work_dir = tempfile.mkdtemp(prefix="slow_run_")
    cwd = os.getcwd()
    os.chdir(work_dir)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            crawler.crawl_website(f"http://127.0.0.1:{port}/", 0, 0, threads=1)
        project = os.path.join(work_dir, f"127_0_0_1_{port}")
        check(os.path.isdir(project), "crawler created an output directory")
        manifest_path = os.path.join(project, "failures.txt")
        check(os.path.isfile(manifest_path), "timeout run produced a manifest")
        with open(manifest_path, encoding="utf-8") as f:
            manifest = f.read()
        check("timeout" in manifest, "slow asset classified as timeout")
        check("/slow.css" in manifest, "timed-out asset's original URL recorded")
        check(os.path.isfile(os.path.join(project, "index.html")),
              "page still saved when one of its assets times out")
        check("Total failed resources: 1" in manifest,
              "exactly one timed-out resource counted")
    finally:
        crawler.TIMEOUT = original_timeout
        httpd.shutdown()
        os.chdir(cwd)
        shutil.rmtree(work_dir, ignore_errors=True)


def verify_crosslink_dedup(tmp_root: str) -> None:
    """Two pages linking to the same target must process it exactly once."""
    print("\n=== Cross-link dedup under concurrency (no double counting) ===")
    site = os.path.join(tmp_root, "xlink_site")
    os.makedirs(site, exist_ok=True)

    def page(name: str, include_a: bool = False) -> str:
        a_link = '<a href="/a.html">a page</a>' if include_a else ""
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
            f'{a_link}'
            '<a href="/shared.html">shared</a>'
            '<a href="/shared-404.html">shared 404</a>'
            f'<img src="/{name}-missing.png">'
            '</body></html>'
        )

    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
        f.write(page("index", include_a=True))
    with open(os.path.join(site, "a.html"), "w", encoding="utf-8") as f:
        f.write(page("a"))
    with open(os.path.join(site, "shared.html"), "w", encoding="utf-8") as f:
        f.write('<!DOCTYPE html><html><body>shared</body></html>')

    base_url, server = start_server(site)
    try:
        for threads in (1, 4, 8):
            work = os.path.join(tmp_root, f"xlink_t{threads}")
            os.makedirs(work, exist_ok=True)
            # Depth 2 so both index and a.html are crawled and both race
            # to enqueue the same shared targets.
            stdout, _ = run_crawler(base_url, work, threads, depth="2")
            project = project_dir_of(work, base_url)

            # Shared reachable page saved once; shared 404 never saved.
            check(os.path.isfile(os.path.join(project, "shared.html")),
                  f"t{threads}: shared reachable page saved")
            check(not os.path.exists(os.path.join(project, "shared-404.html")),
                  f"t{threads}: shared 404 page not saved")

            with open(os.path.join(project, "failures.txt"), encoding="utf-8") as f:
                manifest = f.read()

            # The shared 404 must appear exactly once regardless of thread count.
            shared_404 = urljoin_safe(base_url, "/shared-404.html")
            check(manifest.count(shared_404) == 1,
                  f"t{threads}: shared 404 page counted exactly once")
            check(manifest.count("Total failed pages: 1") == 1,
                  f"t{threads}: exactly one failed page despite cross-links")
            # Each page's own missing image is attributed once to its own page.
            check(manifest.count("index-missing.png") == 1,
                  f"t{threads}: index missing image counted once")
            check(manifest.count("a-missing.png") == 1,
                  f"t{threads}: a-page missing image counted once")
            check(manifest.count("Total failed resources: 2") == 1,
                  f"t{threads}: exactly two failed resources")
            check(manifest.strip() in stdout,
                  f"t{threads}: terminal output matches manifest")
    finally:
        server.close()


def main() -> int:
    tmp_root = tempfile.mkdtemp(prefix="crawler_verify_")
    site_dir = os.path.join(tmp_root, "site")
    closed_port = _free_port()  # never bound -> connection refused
    build_site(site_dir, closed_port)

    base_url, server = start_server(site_dir)
    try:
        single_work = os.path.join(tmp_root, "run_t1")
        multi_work = os.path.join(tmp_root, "run_t4")
        os.makedirs(single_work)
        os.makedirs(multi_work)

        manifest_t1 = verify_run(single_work, base_url, 1)
        manifest_t4 = verify_run(multi_work, base_url, 4)

        print("\n=== Cross-run consistency ===")
        check(manifest_t1 == manifest_t4,
              "manifest identical for single- and multi-threaded runs "
              "(ownership and counts stable under concurrency)")

        verify_all_success(tmp_root, base_url)
        verify_timeout_inprocess()
    finally:
        server.close()

    verify_crosslink_dedup(tmp_root)
    shutil.rmtree(tmp_root, ignore_errors=True)

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
