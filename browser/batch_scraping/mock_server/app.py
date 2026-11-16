"""Local mock of the AgentInBrowser REST server (stdlib only).

Routes intentionally mirror the real service from ``browser/SKILL.md``:

    GET  /status
    GET  /env
    POST /init      {"url": "/pages/index.html"}
    POST /execute   {"cmd": ..., "params": {...}}
    POST /quit
    POST /shutdown
    POST /inject    {"kind": "stale", "target": "window-2"}   # test hook

Run directly::

    python -m mock_server.serve --port 5000 --sites ./sites
"""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from .browser import MockBrowser, MockBrowserError


def create_app(sites_dir: str) -> Tuple[Any, MockBrowser]:
    browser = MockBrowser(sites_dir)

    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")

        def send(payload: Dict[str, Any], status: str = "200 OK"):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            start_response(status, [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ])
            return [body]

        def ok(data: Dict[str, Any]):
            return send({"success": True, "data": data})

        def fail(message: str):
            return send({"success": False, "error": message})

        try:
            if method == "GET" and path == "/status":
                return send({"success": True, "data": browser.status()})
            if method == "GET" and path == "/env":
                return send({"success": True, "data": {
                    "project_dir": sites_dir,
                    "mock": True,
                    "platform": "local-test",
                }})

            if method != "POST":
                return fail(f"unsupported route: {method} {path}")

            payload = _read_json(environ)

            if path == "/init":
                return ok(browser.init(payload.get("url", "/pages/index.html")))
            if path == "/quit":
                return ok(browser.quit())
            if path == "/inject":
                return ok(browser.inject(
                    payload.get("kind", "stale"),
                    payload.get("target"),
                ))
            if path == "/shutdown":
                response = ok({"shutdown": True})
                # Signal the serving loop via environ-bound callable set below.
                if environ.get("mock.shutdown"):
                    environ["mock.shutdown"]()
                return response
            if path != "/execute":
                return fail(f"unknown route: {path}")

            return ok(_dispatch(browser, payload))
        except MockBrowserError as exc:
            return fail(str(exc))
        except KeyError as exc:
            return fail(f"missing required parameter: {exc}")
        except Exception as exc:  # pragma: no cover - defensive envelope
            return fail(f"{type(exc).__name__}: {exc}")

    return app, browser


def _read_json(environ) -> Dict[str, Any]:
    length = int(environ.get("CONTENT_LENGTH") or 0)
    if length == 0:
        return {}
    raw = environ["wsgi.input"].read(length)
    return json.loads(raw.decode("utf-8"))


def _dispatch(browser: MockBrowser, payload: Dict[str, Any]) -> Dict[str, Any]:
    cmd = payload.get("cmd")
    params = payload.get("params") or {}

    if cmd == "find":
        return browser.find(params["selector"])
    if cmd == "inputs":
        return browser.inputs()
    if cmd == "buttons":
        return browser.buttons()
    if cmd == "click":
        return browser.click(int(params["index"]))
    if cmd == "send_keys":
        return browser.send_keys(int(params["index"]), str(params["text"]))
    if cmd == "get_html":
        return browser.get_html(int(params["index"]))
    if cmd == "get_text":
        return browser.get_text()
    if cmd == "page_info":
        return browser.page_info()
    if cmd == "sleep":
        return browser.sleep(float(params.get("seconds", 1)))
    if cmd == "execute_script":
        return browser.execute_script(params["script"])
    if cmd == "switch_window":
        return browser.switch_window(int(params.get("index", -1)))
    if cmd == "close_window":
        return browser.close_window()
    raise MockBrowserError(f"unknown command: {cmd}")
