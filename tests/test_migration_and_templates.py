"""Verify migration of a pre-existing database and Jinja template rendering.

1. Build an old-schema database (pages table without outcome/error_message,
   no unique constraint), run init_db(), and confirm the columns appear and
   old rows default to outcome='ok'.
2. Render report.html / export_report.html with Page objects covering every
   outcome to make sure the new badges/columns work end to end.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="seo-migration-test-")
db_path = Path(_tmp_dir) / "legacy.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}".replace("\\", "/")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import aiosqlite  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from jinja2 import Environment, FileSystemLoader  # noqa: E402

from app.database import init_db, async_session  # noqa: E402
from app.models import Page, PageOutcome  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def main() -> None:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    # 1. Create a legacy-schema pages table plus a row, as older versions did.
    async with aiosqlite.connect(db_path) as legacy:
        await legacy.execute(
            "CREATE TABLE pages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "crawl_job_id INTEGER NOT NULL, "
            "url VARCHAR NOT NULL, "
            "status_code INTEGER, title VARCHAR, meta_description TEXT, "
            "content_length INTEGER, response_time FLOAT, depth INTEGER, "
            "word_count INTEGER, crawled_at DATETIME)"
        )
        await legacy.execute(
            "INSERT INTO pages (crawl_job_id, url, status_code, depth) "
            "VALUES (1, 'http://example.com/old', 200, 0)"
        )
        await legacy.commit()
        cur = await legacy.execute("PRAGMA table_info(pages)")
        cols_before = [r[1] for r in await cur.fetchall()]
    check("outcome" not in cols_before and "error_message" not in cols_before,
          "legacy table starts without the new columns")

    # 2. Run the app's startup migration.
    await init_db()

    async with aiosqlite.connect(db_path) as migrated:
        cur = await migrated.execute("PRAGMA table_info(pages)")
        cols_after = [r[1] for r in await cur.fetchall()]
        cur = await migrated.execute(
            "SELECT outcome, error_message FROM pages WHERE url = ?",
            ("http://example.com/old",),
        )
        row = await cur.fetchone()
    check("outcome" in cols_after and "error_message" in cols_after,
          f"migration adds both columns: {cols_after}")
    check(row == ("ok", None), f"legacy row defaults to outcome='ok' (got {row})")

    # 3. The migrated table accepts new-schema rows for every outcome.
    async with async_session() as db:
        for outcome in PageOutcome:
            db.add(Page(crawl_job_id=1, url=f"http://example.com/{outcome.value}",
                        outcome=outcome, depth=1,
                        status_code=0 if outcome == PageOutcome.FETCH_ERROR else None,
                        error_message="boom" if outcome == PageOutcome.FETCH_ERROR else None))
        await db.commit()
        rows = (await db.execute(select(Page).where(Page.crawl_job_id == 1))).scalars().all()
    check(len(rows) == 5, f"all outcome variants persist on migrated DB (got {len(rows)})")

    # 4. Render the UI report template with one row per outcome.
    templates = Jinja2Templates(directory=str(REPO_ROOT / "templates"))
    class _FakeJob:
        id = 1
        url = "http://example.com"
        pages_crawled = 2
        pages_found = 4
        overall_score = 87.0
        created_at = __import__("datetime").datetime(2026, 1, 1)

    sample_pages = [
        Page(url="http://example.com/ok", outcome=PageOutcome.OK, status_code=200,
             title="Good", word_count=120, response_time=0.42, depth=0),
        Page(url="http://example.com/err", outcome=PageOutcome.FETCH_ERROR, status_code=0,
             word_count=None, response_time=0.01, depth=1, error_message="Server disconnected"),
        Page(url="http://example.com/rob", outcome=PageOutcome.BLOCKED_BY_ROBOTS,
             status_code=None, depth=1),
        Page(url="http://example.com/skip", outcome=PageOutcome.SKIPPED_LIMIT,
             status_code=None, depth=2),
    ]
    html = templates.get_template("report.html").render(
        request={"scope": {}}, job=_FakeJob(), pages=sample_pages,
        issues=[], fixes=[], scores=[],
    )
    for label in ("Fetch error", "Blocked by robots", "Skipped (limit)"):
        check(label in html, f"report.html renders badge '{label}'")
    check("Server disconnected" in html, "report.html shows the fetch error message")
    check("Good" in html and "120" in html, "ok page title/word fields still render")
    check("4 URLs discovered" in html, "report header shows pages_found separately")

    # 5. Render the export template too.
    env = Environment(loader=FileSystemLoader(str(REPO_ROOT / "templates")))
    export_html = env.get_template("export_report.html").render(
        job=_FakeJob(), pages=sample_pages, issues=[], fixes=[], scores=[],
    )
    check("blocked_by_robots" in export_html and "skipped_limit" in export_html,
          "export_report.html renders raw outcome values")
    check("Pages Crawled" in export_html and "URLs Found" in export_html,
          "export report separates crawled/found counters")

    if failures:
        print(f"\n{len(failures)} CHECK(S) FAILED")
        sys.exit(1)
    print("\nALL MIGRATION/TEMPLATE CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
