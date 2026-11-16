"""Thin HTTP client for the AgentInBrowser REST API.

The client intentionally knows nothing about batch policy; it only wraps the
wire protocol documented in ``browser/SKILL.md``::

    POST /init     {"url": ...}
    GET  /status
    POST /execute  {"cmd": ..., "params": {...}}   -> {"success": bool, ...}
    POST /quit
    POST /shutdown

Only the Python standard library is used so the module also runs inside
``python -I`` style restricted environments without third-party packages.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class BrowserAPIError(RuntimeError):
    """Raised when the AgentInBrowser server reports a failed command.

    ``message`` holds the server provided error string so callers can decide
    whether an error is recoverable (stale index, missing element ...).
    """

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.payload = payload or {}


class AIBClient:
    """Synchronous JSON client for an AgentInBrowser server."""

    def __init__(self, base_url: str = "http://127.0.0.1:5000", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ HTTP
    def _post(self, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # server returned 4xx/5xx
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                raise BrowserAPIError(f"HTTP {exc.code}: {raw[:200]}") from exc
        except urllib.error.URLError as exc:
            raise BrowserAPIError(f"cannot reach AgentInBrowser at {self.base_url}: {exc.reason}") from exc
        else:
            payload = json.loads(raw)

        if not payload.get("success", False):
            raise BrowserAPIError(payload.get("error", "unknown server error"), payload)
        return payload

    def _get(self, path: str) -> Dict[str, Any]:
        req = urllib.request.Request(self.base_url + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise BrowserAPIError(f"cannot reach AgentInBrowser at {self.base_url}: {exc.reason}") from exc

    # ------------------------------------------------------------- lifecycle
    def status(self) -> Dict[str, Any]:
        return self._get("/status")

    def init(self, url: str) -> Dict[str, Any]:
        return self._post("/init", {"url": url})

    def quit(self) -> Dict[str, Any]:
        return self._post("/quit", {})

    def shutdown(self) -> Dict[str, Any]:
        return self._post("/shutdown", {})

    # -------------------------------------------------------------- commands
    def execute(self, cmd: str, **params: Any) -> Dict[str, Any]:
        """Run a single ``/execute`` command and return its ``data`` dict.

        Raises :class:`BrowserAPIError` on any ``success: false`` response.
        """
        payload = self._post("/execute", {"cmd": cmd, "params": params})
        return payload.get("data", {}) or {}
