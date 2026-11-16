"""Standalone entry point for the mock AgentInBrowser server."""

from __future__ import annotations

import argparse
import os
import sys
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

if __package__ in (None, ""):  # direct script execution, e.g. python -I serve.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mock_server.app import create_app
else:
    from .app import create_app


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - keep stderr clean
        pass


def serve(host: str, port: int, sites_dir: str):
    app, _browser = create_app(sites_dir)
    httpd = make_server(host, port, app, handler_class=_QuietHandler)

    # The WSGI app runs inside serve_forever's request thread, so it cannot
    # shut the loop down itself. A cross-thread event is set from the request
    # and awaited by the main thread, which then calls httpd.shutdown().
    stop_event = threading.Event()
    httpd.stop_event = stop_event  # exposed for the wrapped WSGI app

    def wrapped(environ, start_response):
        environ["mock.shutdown"] = stop_event.set
        return app(environ, start_response)

    httpd.set_app(wrapped)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print(f"mock AgentInBrowser listening on http://{host}:{port} (sites={sites_dir})")
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        print("mock AgentInBrowser stopped")


def main() -> None:
    default_sites = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sites")
    parser = argparse.ArgumentParser(description="Mock AgentInBrowser server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--sites", default=default_sites)
    args = parser.parse_args()
    serve(args.host, args.port, os.path.abspath(args.sites))


if __name__ == "__main__":
    main()
