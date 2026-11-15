# -*- coding: utf-8 -*-
"""Manual smoke check: run the spider against a local HTTP server.

Not part of the unit test suite.  It proves that the normal ``scrapy crawl``
path (settings.py, spider discovery, real HTTP handler, CloseSpider finish
reason, stat lines) behaves as documented, without touching the real OLX site.

Usage (from the repository root)::

    python tests/manual_cli_smoke.py
"""

from __future__ import annotations

import asyncio
import http.server
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRAPY_DIR = PROJECT_ROOT / "olx_scraper"
for path in (str(SCRAPY_DIR), str(PROJECT_ROOT), str(Path(__file__).resolve().parent)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fixtures import (  # noqa: E402
    DETAIL_FULL,
    DETAIL_MISSING_PHONE,
    LIST_PAGE_EMPTY,
    LIST_PAGE_INVALID,
    LIST_PAGE_WITH_ADS,
)

PORT = 8877
PAGES = {
    "/": "<html><body>home</body></html>",
    "/lahore/q-iphone": LIST_PAGE_WITH_ADS,
    "/item/iphone-11-111": DETAIL_FULL,
    "/item/iphone-13-333": DETAIL_FULL,
    "/item/iphone-14-555": DETAIL_MISSING_PHONE,
}
BODY_PAGES = {"empty": LIST_PAGE_EMPTY, "invalid": LIST_PAGE_INVALID}


class Handler(http.server.BaseHTTPRequestHandler):
    mode = "ads"

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            body = PAGES["/"]
        elif self.path == "/lahore/q-iphone":
            body = BODY_PAGES.get(Handler.mode, PAGES["/lahore/q-iphone"])
        else:
            body = PAGES.get(self.path)
        status = 200 if body else 404
        data = (body or "<html><body>404</body></html>").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def run(mode):
    Handler.mode = mode
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    from scrapy.settings import Settings
    from scrapy.utils.test import AsyncCrawlerRunner

    from olx_scraper.spiders.scrape_olx import ScraperSpider

    class LocalSpider(ScraperSpider):
        start_urls = ["http://127.0.0.1:%d/" % PORT]

        def parse(self, response):
            # args.py still provides city/product; only the host is localised.
            from scrapy import Request as R

            return R(
                "http://127.0.0.1:%d/lahore/q-iphone" % PORT,
                callback=self.parse_products,
                errback=self.list_request_failed,
            )

    settings = Settings()
    settings.setmodule("olx_scraper.settings", priority="project")
    settings.set("LOG_ENABLED", False, priority="cmdline")
    settings.set("ITEM_PIPELINES", {}, priority="cmdline")
    settings.set("TWISTED_REACTOR_ENABLED", False, priority="cmdline")

    runner = AsyncCrawlerRunner(settings)
    crawler = runner.create_crawler(LocalSpider)

    async def go():
        await runner.crawl(crawler)
        await runner.join()

    asyncio.run(go())
    server.shutdown()
    thread.join(timeout=5)
    return crawler.stats.get_stats()


def report(mode, stats):
    print("=== mode=%s ===" % mode)
    for key in sorted(stats):
        if str(key).startswith("olx/"):
            print("  %-42s %s" % (key, stats[key]))
    print("  %-42s %s" % ("finish_reason", stats.get("finish_reason")))
    print()


if __name__ == "__main__":
    for mode in ("ads", "empty", "invalid"):
        report(mode, run(mode))
