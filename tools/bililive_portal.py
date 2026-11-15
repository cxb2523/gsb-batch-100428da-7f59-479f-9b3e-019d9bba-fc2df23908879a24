#!/usr/bin/env python3
"""
Proxy bililive-go and inject a dashboard entry into its HTML shell.
"""

import os
from urllib.parse import urljoin

import aiohttp
from aiohttp import web


UPSTREAM = os.getenv("BILILIVE_PORTAL_UPSTREAM", "http://127.0.0.1:18081")
DASHBOARD_URL = os.getenv("BILILIVE_PORTAL_DASHBOARD_URL", "http://localhost:8010/dashboard")
BIND_HOST = os.getenv("BILILIVE_PORTAL_HOST", "0.0.0.0")
BIND_PORT = int(os.getenv("BILILIVE_PORTAL_PORT", "18080"))


INJECT = f"""
<style>
  .douyin-data-entry {{
    position: fixed;
    right: 18px;
    top: 14px;
    z-index: 99999;
    height: 36px;
    padding: 0 14px;
    border-radius: 6px;
    background: #111827;
    color: #fff !important;
    display: inline-flex;
    align-items: center;
    font: 600 13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    text-decoration: none;
    box-shadow: 0 8px 24px rgba(0,0,0,.18);
  }}
  .douyin-data-entry:hover {{ background: #0f766e; }}
</style>
<a class="douyin-data-entry" href="{DASHBOARD_URL}">直播数据</a>
"""


async def proxy(request: web.Request) -> web.StreamResponse:
    target = urljoin(UPSTREAM + "/", request.match_info["path"])
    if request.query_string:
        target += "?" + request.query_string

    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "accept-encoding", "content-length"}}
    body = await request.read()
    async with aiohttp.ClientSession() as session:
        async with session.request(request.method, target, headers=headers, data=body, allow_redirects=False) as resp:
            content = await resp.read()
            response_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in {"content-length", "content-encoding", "transfer-encoding", "connection"}
            }
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" in content_type:
                text = content.decode(resp.charset or "utf-8", errors="ignore")
                text = text.replace("</body>", INJECT + "</body>")
                content = text.encode("utf-8")
                response_headers["Content-Type"] = "text/html; charset=utf-8"
            return web.Response(status=resp.status, headers=response_headers, body=content)


def main():
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", proxy)
    web.run_app(app, host=BIND_HOST, port=BIND_PORT)


if __name__ == "__main__":
    main()
