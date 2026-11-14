"""Concurrency stress: high parallelism + low max_pages must never overshoot
the cap or insert duplicate rows, even when every page links every other page.
"""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="seo-stress-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(_tmp_dir) / 'stress.db'}".replace("\\", "/")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aiohttp import web  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import init_db, async_session  # noqa: E402
from app.models import CrawlJob, Page, CrawlStatus, PageOutcome  # noqa: E402
from app.crawler.engine import run_crawl  # noqa: E402

settings.POLITENESS_DELAY = 0
settings.MAX_CONCURRENT_REQUESTS = 10

PAGE_COUNT = 25
MAX_PAGES = 7


def build_pages() -> dict:
    # Every page links every other page -> massive duplicate discovery.
    links = "".join(f'<a href="/p{i}">{i}</a>' for i in range(PAGE_COUNT))
    pages = {
        "/": f'<html><head><title>Home</title></head><body>{links}</body></html>',
    }
    for i in range(PAGE_COUNT):
        pages[f"/p{i}"] = f'<html><head><title>P{i}</title></head><body>{links}</body></html>'
    return pages


PAGES = build_pages()


async def handler(request):
    if request.path in PAGES:
        return web.Response(text=PAGES[request.path], content_type="text/html")
    return web.Response(status=404)


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

    port = free_port()

    async def robots_handler(request):
        return web.Response(text="User-agent: *\n")

    async def no_sitemap(request):
        return web.Response(status=404)

    app = web.Application()
    app.router.add_get("/robots.txt", robots_handler)
    app.router.add_get("/sitemap.xml", no_sitemap)
    for path in PAGES:
        app.router.add_get(path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    base = f"http://127.0.0.1:{port}"

    try:
        await init_db()
        async with async_session() as db:
            job = CrawlJob(url=base, status=CrawlStatus.CRAWLING,
                           max_pages=MAX_PAGES, max_depth=3)
            db.add(job)
            await db.commit()
            await db.refresh(job)
            job_id = job.id

        await run_crawl(job_id, async_session, base,
                        max_pages=MAX_PAGES, max_depth=3)

        async with async_session() as db:
            job = await db.get(CrawlJob, job_id)
            rows = (await db.execute(
                select(Page).where(Page.crawl_job_id == job_id)
            )).scalars().all()

        urls = [r.url for r in rows]
        ok = [r for r in rows if r.outcome == PageOutcome.OK]
        skipped = [r for r in rows if r.outcome == PageOutcome.SKIPPED_LIMIT]

        print(f"\nrows={len(rows)} unique={len(set(urls))} "
              f"ok={len(ok)} skipped={len(skipped)} "
              f"job.crawled={job.pages_crawled} found={job.pages_found}")

        check(len(urls) == len(set(urls)), "no duplicate rows despite cross-linked discovery")
        check(len(ok) == MAX_PAGES, f"ok rows exactly max_pages ({len(ok)} == {MAX_PAGES})")
        check(job.pages_crawled == MAX_PAGES,
              f"pages_crawled exactly max_pages ({job.pages_crawled}) — no concurrency overshoot")
        check(len(rows) == job.pages_found,
              "every discovered URL has one traceable row (pages_found == rows)")
        check(len(ok) + len(skipped) == len(rows), "every row is ok or skipped_limit")
        check(all(r.status_code == 200 for r in ok), "all ok rows fetched with status 200")
        check(all(r.status_code is None for r in skipped), "skipped rows were never fetched")
    finally:
        await runner.cleanup()

    if failures:
        print(f"\n{len(failures)} CHECK(S) FAILED")
        sys.exit(1)
    print("\nALL STRESS CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
