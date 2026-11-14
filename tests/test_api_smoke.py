"""HTTP-level smoke test: real FastAPI app + real aiohttp target server.

Drives the public API (start crawl, SSE progress, report HTML page, JSON
report, pages endpoint) and verifies outcome data is exposed end to end.
"""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="seo-smoke-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(_tmp_dir) / 'smoke.db'}".replace("\\", "/")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aiohttp import web as aio_web  # noqa: E402
import httpx  # noqa: E402

from app.config import settings  # noqa: E402

settings.POLITENESS_DELAY = 0

PAGES = {
    "/": ('<html><head><title>Home</title></head><body>'
          '<a href="/blocked">b</a><a href="/dead">d</a>'
          '<a href="/a">a</a><a href="/duplicate">x</a><a href="/extra">e</a></body></html>'),
    "/a": '<html><head><title>A</title></head><body>alpha</body></html>',
    "/duplicate": '<html><head><title>Dup</title></head><body>dup</body></html>',
    "/extra": '<html><head><title>Extra</title></head><body>stays queued past the cap</body></html>',
    "/blocked": "<html>nope</html>",
}


async def page_handler(request):
    if request.path in PAGES:
        return aio_web.Response(text=PAGES[request.path], content_type="text/html")
    return aio_web.Response(status=404)


async def dead_handler(request):
    request.transport.close()
    return aio_web.StreamResponse()


async def robots_handler(request):
    return aio_web.Response(text="User-agent: *\nDisallow: /blocked\n")


async def sitemap_handler(request):
    base = f"http://127.0.0.1:{request.app['port']}"
    return aio_web.Response(
        text=f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             f'<url><loc>{base}/duplicate</loc></url></urlset>',
        content_type="application/xml",
    )


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def main():
    failures = []

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    target_port = free_port()
    target = aio_web.Application()
    target["port"] = target_port
    target.router.add_get("/robots.txt", robots_handler)
    target.router.add_get("/sitemap.xml", sitemap_handler)
    target.router.add_get("/sitemap_index.xml", sitemap_handler)
    target.router.add_get("/dead", dead_handler)
    for p in PAGES:
        target.router.add_get(p, page_handler)
    runner = aio_web.AppRunner(target)
    await runner.setup()
    await aio_web.TCPSite(runner, "127.0.0.1", target_port).start()
    base = f"http://127.0.0.1:{target_port}"

    # Boot the real FastAPI app with ASGI in-process transport.
    from app.main import app
    from app.database import init_db
    await init_db()  # ASGITransport does not run lifespan handlers
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        resp = await client.post("/api/crawl/", json={"url": base, "max_pages": 3, "max_depth": 2})
        check(resp.status_code == 200, f"start crawl API returns 200 (got {resp.status_code})")
        job = resp.json()
        job_id = job["id"]

        # Poll until complete.
        final_job = None
        for _ in range(60):
            await asyncio.sleep(0.5)
            r = await client.get(f"/api/crawl/{job_id}")
            final_job = r.json()
            if final_job["status"] in ("complete", "failed"):
                break
        check(final_job and final_job["status"] == "complete",
              f"crawl completes (status={final_job and final_job['status']}, "
              f"err={final_job and final_job.get('error_message')})")
        print(f"  job: crawled={final_job['pages_crawled']} found={final_job['pages_found']}")
        # Fetchable pages are /, /a, /duplicate (3), but blocked/dead free up
        # slots they briefly reserved, so assert against observed ok rows and
        # prove non-ok outcomes never inflate the counter.
        pages_preview = (await client.get(f"/api/reports/{job_id}/pages")).json()
        ok_count = sum(1 for p in pages_preview if p["outcome"] == "ok")
        check(final_job["pages_crawled"] == ok_count and ok_count <= 3,
              f"pages_crawled ({final_job['pages_crawled']}) equals ok rows ({ok_count}), cap respected")
        check(final_job["pages_found"] >= 5, "pages_found counts all discovered URLs")

        # JSON pages endpoint exposes outcome.
        pages = (await client.get(f"/api/reports/{job_id}/pages")).json()
        outcomes = {p["outcome"] for p in pages}
        check({"blocked_by_robots", "fetch_error", "ok"} <= outcomes,
              f"pages API exposes outcomes: {sorted(outcomes)}")
        check(all("error_message" in p for p in pages), "pages API includes error_message field")
        check(len(pages) == len({p["url"] for p in pages}), "no duplicate page rows via API")
        err_page = next((p for p in pages if p["outcome"] == "fetch_error"), None)
        check(err_page is not None and err_page["status_code"] == 0 and err_page["error_message"],
              "fetch_error row carries status_code=0 and error message")

        # Full report JSON.
        report = (await client.get(f"/api/reports/{job_id}")).json()
        check(report["job"]["pages_found"] == len(report["pages"]),
              "report job.pages_found matches number of page records")

        # HTML report page renders.
        html = (await client.get(f"/report/{job_id}")).text
        check("Fetch error" in html and "Blocked by robots" in html,
              "HTML report shows outcome badges")
        check("URLs discovered" in html, "HTML report shows discovered-URL count")

        # HTML export renders.
        export_html = (await client.get(f"/export/{job_id}/html")).text
        check("fetch_error" in export_html and "blocked_by_robots" in export_html,
              "HTML export contains outcome values")

    await runner.cleanup()
    if failures:
        print(f"\n{len(failures)} CHECK(S) FAILED")
        sys.exit(1)
    print("\nALL SMOKE CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
