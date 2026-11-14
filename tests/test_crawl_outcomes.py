"""End-to-end test for per-URL crawl outcome tracking.

Starts a local aiohttp server that serves:
  - robots.txt disallowing /blocked
  - sitemap.xml listing /duplicate (also linked from pages)
  - pages with internal links, one duplicated discovery path
  - an unreachable link (closed port) -> fetch_error

Then runs the real crawl engine against an isolated SQLite database and
asserts every queued URL gets exactly one Page row with the correct outcome,
while pages_crawled counts only successful fetches.
"""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web
from sqlalchemy import select

# Isolate the database BEFORE importing app modules that read settings.
_tmp_dir = tempfile.mkdtemp(prefix="seo-crawl-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(_tmp_dir) / 'test.db'}".replace("\\", "/")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import settings  # noqa: E402
from app.database import init_db, async_session  # noqa: E402
from app.models import CrawlJob, Page, CrawlStatus, PageOutcome  # noqa: E402
from app.crawler.engine import run_crawl, crawl_progress  # noqa: E402

settings.POLITENESS_DELAY = 0
settings.MAX_CONCURRENT_REQUESTS = 2

PAGES = {
    # Link order matters: blocked/dead are linked first so they are reached
    # before the max_pages budget is consumed by ordinary pages.
    "/": """<html><head><title>Home</title><meta name="description" content="home desc"></head>
        <body><h1>Home</h1>
        <a href="/blocked">Blocked</a><a href="/dead">Dead</a>
        <a href="/a">A</a><a href="/duplicate">Dup</a><a href="/b">B</a>
        <p>word "word" "word"</p></body></html>""",
    "/a": '<html><head><title>A page</title></head><body><a href="/c">C</a> A content</body></html>',
    "/b": '<html><head><title>B page</title></head><body>B content</body></html>',
    "/c": '<html><head><title>C page</title></head><body>C content stays queued past the budget</body></html>',
    "/duplicate": '<html><head><title>Dup</title></head><body>duplicate discovery path</body></html>',
    "/blocked": '<html><body>should never be fetched</body></html>',
}

ROBOTS_TXT = "User-agent: *\nDisallow: /blocked\n"
# /duplicate is also linked from the home page: sitemap + internal-link
# discovery must collapse into a single row.
SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/duplicate</loc></url>
</urlset>"""


async def handle_page(request: web.Request) -> web.Response:
    path = request.path
    if path in PAGES:
        return web.Response(text=PAGES[path], content_type="text/html")
    return web.Response(status=404, text="not found")


async def handle_dead(request: web.Request) -> web.StreamResponse:
    # Close the connection without sending a response so the client sees a
    # transport-level error (aiohttp raises ServerDisconnectedError ->
    # fetch_url returns status_code=0 with an error message).
    request.transport.close()
    return web.StreamResponse()


async def handle_robots(request: web.Request) -> web.Response:
    return web.Response(text=ROBOTS_TXT, content_type="text/plain")


async def handle_sitemap(request: web.Request) -> web.Response:
    base = f"http://127.0.0.1:{request.app['port']}"
    return web.Response(text=SITEMAP_XML.format(base=base), content_type="application/xml")


def find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def main() -> None:
    port = find_free_port()

    app = web.Application()
    app["port"] = port
    app.router.add_get("/robots.txt", handle_robots)
    app.router.add_get("/sitemap.xml", handle_sitemap)
    app.router.add_get("/sitemap_index.xml", handle_sitemap)
    app.router.add_get("/dead", handle_dead)
    for path in PAGES:
        app.router.add_get(path, handle_page)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    base = f"http://127.0.0.1:{port}"
    dead_url = base + "/dead"

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    try:
        await init_db()

        async with async_session() as db:
            job = CrawlJob(url=base, status=CrawlStatus.CRAWLING, max_pages=3, max_depth=3)
            db.add(job)
            await db.commit()
            await db.refresh(job)
            job_id = job.id

        await run_crawl(job_id, async_session, base, max_pages=3, max_depth=3)

        async with async_session() as db:
            job = await db.get(CrawlJob, job_id)
            pages = (await db.execute(
                select(Page).where(Page.crawl_job_id == job_id)
            )).scalars().all()

        by_url = {p.url: p for p in pages}
        outcomes = {p.outcome for p in pages}
        counts: dict[PageOutcome, int] = {}
        for p in pages:
            counts[p.outcome] = counts.get(p.outcome, 0) + 1

        print(f"\nJob: crawled={job.pages_crawled} found={job.pages_found} status={job.status.value}")
        for p in sorted(pages, key=lambda x: x.url):
            print(f"  {p.outcome.value:18s} {p.url}  status={p.status_code} "
                  f"depth={p.depth} err={p.error_message!r}")

        # 1. One row per discovered URL, no duplicates.
        check(len(pages) == len(by_url), "one Page row per unique URL")
        check(job.pages_found == len(pages),
              f"pages_found ({job.pages_found}) == total page rows ({len(pages)})")

        # 2. All four outcomes are represented.
        check(outcomes == set(PageOutcome), f"all four outcomes present: {sorted(o.value for o in outcomes)}")

        # 3. Only successful fetches count as crawled. Five URLs are fetchable
        #    (/, /a, /b, /c, /duplicate) but the cap is 3; blocked, error and
        #    skipped outcomes never inflate the counter.
        ok_rows = [p for p in pages if p.outcome == PageOutcome.OK]
        check(job.pages_crawled == len(ok_rows) == 3,
              f"pages_crawled ({job.pages_crawled}) == ok rows ({len(ok_rows)}) == max_pages cap")
        check(job.pages_crawled <= 3, "pages_crawled never exceeds max_pages")

        # 4. OK rows keep title/word/response fields intact.
        home = by_url.get(base + "/")
        check(home is not None and home.outcome == PageOutcome.OK, "home page recorded as ok")
        check(home is not None and home.title == "Home", "ok row preserves title")
        check(home is not None and home.word_count is not None and home.word_count > 0,
              "ok row preserves word_count")
        check(home is not None and home.response_time is not None and home.response_time >= 0,
              "ok row preserves response_time")
        check(home is not None and home.status_code == 200, "ok row keeps HTTP status code")

        # 5. Robots-blocked URL is stored but not crawled.
        blocked = by_url.get(base + "/blocked")
        check(blocked is not None and blocked.outcome == PageOutcome.BLOCKED_BY_ROBOTS,
              "robots-blocked URL stored with blocked_by_robots outcome")
        check(blocked is not None and blocked.status_code is None and blocked.title is None,
              "blocked row has no fetch-derived fields")

        # 6. Fetch error keeps status_code=0 and the error message.
        dead = by_url.get(dead_url)
        check(dead is not None and dead.outcome == PageOutcome.FETCH_ERROR,
              "unreachable URL stored with fetch_error outcome")
        check(dead is not None and dead.status_code == 0, "fetch_error row keeps status_code=0")
        check(dead is not None and bool(dead.error_message), "fetch_error row stores error_message")

        # 7. Over-limit URLs are stored as skipped_limit and were never fetched.
        skipped = [p for p in pages if p.outcome == PageOutcome.SKIPPED_LIMIT]
        check(len(skipped) >= 1, f"at least one skipped_limit row (got {len(skipped)})")
        check(base + "/c" in {p.url for p in skipped},
              "the late-queued /c URL is recorded as skipped_limit")
        check(all(p.status_code is None for p in skipped), "skipped rows were never fetched")
        check(job.pages_crawled == 3 and all(p.url not in {r.url for r in ok_rows} for p in skipped),
              "skipped rows do not inflate pages_crawled")
        # 8. Duplicate discovery (sitemap + internal link) yields one row.
        dup = by_url.get(base + "/duplicate")
        check(dup is not None and dup.outcome == PageOutcome.OK, "duplicated URL still has one ok row")
        dup_count = sum(1 for p in pages if p.url == base + "/duplicate")
        check(dup_count == 1, f"duplicated URL inserted exactly once (got {dup_count})")

        # 9. DB-level unique constraint exists for fresh databases.
        async with async_session() as db:
            from sqlalchemy import inspect as sa_inspect
            def _inspect(sync_session):
                insp = sa_inspect(sync_session.bind)
                return {u["name"]: u["column_names"]
                        for u in insp.get_unique_constraints("pages")}
            unique = await db.run_sync(_inspect)
        check(any(set(cols) == {"crawl_job_id", "url"} for cols in unique.values()),
              f"unique constraint on (crawl_job_id, url) present: {unique}")

        # 10. Progress dict matches final counters.
        progress = crawl_progress[job_id]
        check(progress["pages_crawled"] == job.pages_crawled, "progress pages_crawled matches job")
        check(progress["pages_found"] == job.pages_found, "progress pages_found matches job")
        check(progress["status"] == "complete", "progress ends as complete")

        # 11. Schema serialization exposes outcome.
        from app.schemas import PageResponse
        serialized = PageResponse.model_validate(dead)
        check(serialized.outcome == "fetch_error" and serialized.error_message,
              "PageResponse exposes outcome and error_message")

        # 12. A second job reusing the same URLs does not collide across jobs.
        async with async_session() as db:
            job2 = CrawlJob(url=base, status=CrawlStatus.CRAWLING, max_pages=1, max_depth=1)
            db.add(job2)
            await db.commit()
            await db.refresh(job2)
            job2_id = job2.id
        await run_crawl(job2_id, async_session, base, max_pages=1, max_depth=1)
        async with async_session() as db:
            job2_rows = (await db.execute(
                select(Page).where(Page.crawl_job_id == job2_id)
            )).scalars().all()
        check(len(job2_rows) >= 1 and all(p.crawl_job_id == job2_id for p in job2_rows),
              "second job creates its own rows without unique-constraint collision")

        # 13. Explicit duplicate insert is rejected by the constraint.
        from sqlalchemy.exc import IntegrityError
        async with async_session() as db:
            db.add(Page(crawl_job_id=job_id, url=base + "/", outcome=PageOutcome.OK))
            try:
                await db.commit()
                check(False, "duplicate (job, url) insert rejected by constraint")
            except IntegrityError:
                await db.rollback()
                check(True, "duplicate (job, url) insert rejected by constraint")

        total_rows = len(pages) + len(job2_rows)
        print(f"\nTotal Page rows across both jobs: {total_rows}")
    finally:
        await runner.cleanup()

    if failures:
        print(f"\n{len(failures)} CHECK(S) FAILED")
        sys.exit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
