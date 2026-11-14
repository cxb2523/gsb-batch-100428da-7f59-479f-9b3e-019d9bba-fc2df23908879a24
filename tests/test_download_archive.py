import asyncio
import os
import logging

import pytest
from aiohttp import web

from site_converter.downloader import (
    AsyncDownloader,
    DownloadStatus,
    format_summary,
)
from site_converter.parser import SiteParser


@pytest.fixture
def output_dir(tmp_path):
    return str(tmp_path / "offline")


async def _start_server(app) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner


def _port(runner: web.AppRunner) -> int:
    return runner.addresses[0][1]


@pytest.mark.asyncio
async def test_download_collision_failure_and_skip(output_dir):
    hits = {"slow": 0}

    async def logo(request):
        return web.Response(body=b"logo-" + request.query.get("v", "0").encode())

    async def missing(request):
        return web.Response(status=404, text="not found")

    async def slow(request):
        hits["slow"] += 1
        await asyncio.sleep(1.5)
        return web.Response(body=b"slow")

    app = web.Application()
    app.router.add_get("/logo.png", logo)
    app.router.add_get("/missing.png", missing)
    app.router.add_get("/slow.js", slow)
    runner = await _start_server(app)
    base = f"http://127.0.0.1:{_port(runner)}"

    try:
        # Seed an existing file to exercise the skip path.
        os.makedirs(os.path.join(output_dir, "images"), exist_ok=True)
        with open(os.path.join(output_dir, "images", "logo.png"), "wb") as f:
            f.write(b"pre-existing")

        assets = {
            (f"{base}/logo.png?v=1", "images"),
            (f"{base}/logo.png?v=2", "images"),
            (f"{base}/missing.png", "images"),
            (f"{base}/slow.js", "scripts"),
        }

        async with AsyncDownloader(output_dir, timeout=0.3) as downloader:
            results = await downloader.download_all(assets)
    finally:
        await runner.cleanup()

    by_url = {r.url: r for r in results}

    first = by_url[f"{base}/logo.png?v=1"]
    second = by_url[f"{base}/logo.png?v=2"]
    assert first.status is DownloadStatus.SKIPPED
    assert first.local_path == "images/logo.png"

    # The second URL must not overwrite the skipped file; it gets a distinct
    # path (or, on a repeat run, is itself skipped at that distinct path).
    assert second.ok
    assert second.local_path != first.local_path
    assert os.path.exists(os.path.join(output_dir, *second.local_path.split("/")))

    missing = by_url[f"{base}/missing.png"]
    assert missing.status is DownloadStatus.HTTP_ERROR
    assert missing.local_path is None
    assert "404" in missing.detail

    slow = by_url[f"{base}/slow.js"]
    assert slow.status is DownloadStatus.TIMEOUT
    assert slow.local_path is None
    assert not os.path.exists(os.path.join(output_dir, "scripts", "slow.js"))

    success = sum(1 for r in results if r.status is DownloadStatus.SUCCESS)
    skipped = sum(1 for r in results if r.status is DownloadStatus.SKIPPED)
    failed = sum(1 for r in results if r.failed)
    assert (success, skipped, failed) == (1, 1, 2)

    summary = format_summary(results)
    assert "Success: 1" in summary
    assert "Skipped: 1" in summary
    assert "Failed:  2" in summary
    assert f"{base}/missing.png" in summary
    assert "HTTP 404" in summary
    assert f"{base}/slow.js" in summary


@pytest.mark.asyncio
async def test_collision_produces_distinct_files(tmp_path):
    async def logo(request):
        return web.Response(body=request.query.get("v", "0").encode())

    app = web.Application()
    app.router.add_get("/logo.png", logo)
    runner = await _start_server(app)
    base = f"http://127.0.0.1:{_port(runner)}"
    out = str(tmp_path / "offline")

    try:
        async with AsyncDownloader(out) as downloader:
            results = await downloader.download_all(
                {(f"{base}/logo.png?v=1", "images"), (f"{base}/logo.png?v=2", "images")}
            )
    finally:
        await runner.cleanup()

    assert all(r.status is DownloadStatus.SUCCESS for r in results)
    paths = {r.local_path for r in results}
    assert len(paths) == 2
    for result in results:
        assert os.path.exists(os.path.join(out, *result.local_path.split("/")))
    # Each URL's bytes landed in its own file.
    for result in results:
        with open(os.path.join(out, *result.local_path.split("/")), "rb") as f:
            assert f.read() in (b"1", b"2")


@pytest.mark.asyncio
async def test_derive_relative_path_is_safe_and_consistent(tmp_path):
    derive = AsyncDownloader._derive_relative_path
    assert derive("http://example.com/a/b.png", "images") == "images/a/b.png"
    assert derive("http://example.com/a%20b/c%20d.png", "images") == "images/a b/c d.png"
    assert derive("http://example.com/", "misc") == "misc/index.html"
    # Traversal segments must not escape the output directory.
    assert derive("http://example.com/../../etc/passwd", "misc") == "misc/etc/passwd"
    assert derive("http://example.com/a%5Cb%3Ac.png", "images") == "images/a_b_c.png"


@pytest.mark.asyncio
async def test_process_site_rewrites_only_successful_assets(tmp_path, caplog):
    async def ok_png(request):
        return web.Response(body=b"png", content_type="image/png")

    app = web.Application()
    app.router.add_get("/ok.png", ok_png)
    runner = await _start_server(app)
    base = f"http://127.0.0.1:{_port(runner)}"
    out = str(tmp_path / "offline")

    html = (
        "<html><body>"
        f'<img src="{base}/ok.png">'
        f'<img src="{base}/broken.png">'
        "</body></html>"
    )
    html_path = tmp_path / "index.html"
    html_path.write_text(html, encoding="utf-8")

    try:
        parser = SiteParser(output_dir=out)
        with caplog.at_level(logging.INFO):
            await parser.process_site(str(html_path))
    finally:
        await runner.cleanup()

    out_html = (tmp_path / "offline" / "index_offline.html").read_text(encoding="utf-8")
    assert "images/ok.png" in out_html
    assert f"{base}/broken.png" in out_html
    assert "images/broken.png" not in out_html

    log = caplog.text
    assert "Success: 1" in log
    assert "Failed:  1" in log
    assert f"{base}/broken.png" in log
    assert "HTTP 404" in log
