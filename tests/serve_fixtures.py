# -*- coding: utf-8 -*-
"""Serve fixture HTML on localhost for a real ``scrapy crawl`` smoke run.

Start it in the background, then run::

    cd olx_scraper
    scrapy crawl scrape_olx -a base_url=http://127.0.0.1:8877/ -s ITEM_PIPELINES={}

Use ``MODE=ads|empty|invalid`` to choose which list page is served.
"""

from __future__ import annotations

import http.server
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from fixtures import (  # noqa: E402
    DETAIL_FULL,
    DETAIL_MISSING_DESCRIPTION,
    DETAIL_MISSING_PHONE,
    LIST_PAGE_EMPTY,
    LIST_PAGE_INVALID,
    LIST_PAGE_WITH_ADS,
)

PORT = int(os.environ.get("PORT", "8877"))
MODE = os.environ.get("MODE", "ads")

PAGES = {
    "/": "<html><body>home</body></html>",
    "/lahore/q-iphone": {
        "ads": LIST_PAGE_WITH_ADS,
        "empty": LIST_PAGE_EMPTY,
        "invalid": LIST_PAGE_INVALID,
    }[MODE],
    "/item/iphone-11-111": DETAIL_FULL,
    "/item/iphone-13-333": DETAIL_MISSING_DESCRIPTION,
    "/item/iphone-14-555": DETAIL_MISSING_PHONE,
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = PAGES.get(self.path.split("?")[0])
        status = 200 if body else 404
        data = (body or "<html><body>404</body></html>").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("serving %s on http://127.0.0.1:%d/" % (MODE, PORT), flush=True)
    server.serve_forever()
