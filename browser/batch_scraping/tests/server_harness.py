"""Start the mock AgentInBrowser server on an ephemeral local port.

Uses wsgiref in a daemon thread so the whole verification stays in one
Python process, needs no browser and never touches a real web site.
"""

from __future__ import annotations

import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

from mock_server.app import create_app


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass


class MockServerHandle:
    def __init__(self, sites_dir: str):
        self.app, self.browser = create_app(sites_dir)
        self.httpd = make_server("127.0.0.1", 0, self.app,
                                 handler_class=_QuietHandler)
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        daemon=True)

    def __enter__(self) -> "MockServerHandle":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)
