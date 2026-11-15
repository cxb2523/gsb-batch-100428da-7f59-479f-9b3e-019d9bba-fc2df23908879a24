"""sing-box lifecycle management.

Everything sing-box specific lives here: binary discovery, config
generation and validation, process supervision, readiness detection,
Clash API communication, and cleanup. The rest of the program only sees
``start()`` / ``select()`` / ``verify_current()`` / ``stop()``.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .health import DEFAULT_CHECK_URL
from .logging import get_logger
from .models import Proxy


class SingBoxError(RuntimeError):
    """Raised for any sing-box discovery/config/startup/API failure."""


SELECTOR_TAG = "proxy-rotate"
INBOUND_TAG = "proxy-in"
PROCESS_REDIRECT_TAG = "process-redirect"

_COMMON_BIN_LOCATIONS = (
    "~/sing-box/sing-box",
    "~/.local/bin/sing-box",
    "/usr/local/bin/sing-box",
    "/usr/bin/sing-box",
    "/opt/homebrew/bin/sing-box",
)

_START_TIMEOUT = 15.0

def discover_binary(explicit: str | None) -> str:
    """Locate the sing-box executable.

    Order: explicit path, then $PATH, then common install locations.
    Shell aliases are never used and nothing is ever invoked via sudo.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise SingBoxError(
            f"sing-box executable not found at: {path}\n"
            "\n"
            "Install sing-box, or check the path:\n"
            "\n"
            "  proxyrotate --sing-box /path/to/sing-box <command>"
        )

    found = shutil.which("sing-box")
    if found:
        return found

    for candidate in _COMMON_BIN_LOCATIONS:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    raise SingBoxError(
        "sing-box executable not found.\n"
        "\n"
        "Install sing-box, or tell ProxyRotate where it is:\n"
        "\n"
        "  proxyrotate --sing-box /path/to/sing-box <command>"
    )


def _free_port() -> int:
    """Ask the OS for a free TCP port on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class SingBoxManager:
    """Owns one sing-box instance fronting a pool of proxy outbounds."""

    def __init__(
        self,
        proxies: list[Proxy],
        binary: str,
        api_port: int | None = None,
        inbound_port: int | None = None,
        logger=None,
        redirect_port: int | None = None,
    ):
        """Create one sing-box manager.

        ``redirect_port`` (used by process routing) adds a transparent
        ``redirect`` inbound next to the ``mixed`` inbound; plain
        ProxyRotate runs leave it ``None`` and the config is unchanged.
        """
        if not proxies:
            raise ValueError("at least one proxy is required")

        self.log = logger or get_logger("singbox")
        self.binary = binary
        self.proxies = list(proxies)
        self.api_port = api_port or _free_port()
        self.inbound_port = inbound_port or _free_port()
        self.redirect_port = redirect_port
        self.secret = secrets.token_hex(16)

        self.config_file: Path | None = None
        self._stderr_file = None
        self._process: subprocess.Popen | None = None

    # -- configuration -------------------------------------------------------

    def build_config(self) -> dict:
        """Build the sing-box configuration dictionary."""
        outbounds: list[dict] = []
        tags: list[str] = []

        for index, proxy in enumerate(self.proxies):
            tag = f"proxy-{index}"
            tags.append(tag)

            if proxy.scheme in ("http", "https"):
                outbound = {
                    "type": "http",
                    "tag": tag,
                    "server": proxy.host,
                    "server_port": proxy.port,
                }
            elif proxy.scheme == "socks5":
                outbound = {
                    "type": "socks",
                    "tag": tag,
                    "server": proxy.host,
                    "server_port": proxy.port,
                    "version": "5",
                }
            else:  # pragma: no cover - parse() rejects other schemes earlier
                raise SingBoxError(f"unsupported scheme: {proxy.scheme}")

            if proxy.username is not None:
                outbound["username"] = proxy.username
            if proxy.password is not None:
                outbound["password"] = proxy.password

            outbounds.append(outbound)

        outbounds.append(
            {
                "type": "selector",
                "tag": SELECTOR_TAG,
                "outbounds": tags,
                "default": tags[0],
            }
        )
        outbounds.append({"type": "direct", "tag": "direct"})

        inbounds = [
            {
                "type": "mixed",
                "tag": INBOUND_TAG,
                "listen": "127.0.0.1",
                "listen_port": self.inbound_port,
            }
        ]
        if self.redirect_port is not None:
            # Transparent inbound for process routing: nftables REDIRECTs
            # the routed cgroup's TCP here and SO_ORIGINAL_DST recovers
            # the real destination; the selector decides the outbound.
            inbounds.append(
                {
                    "type": "redirect",
                    "tag": PROCESS_REDIRECT_TAG,
                    "listen": "127.0.0.1",
                    "listen_port": self.redirect_port,
                }
            )

        return {
            "log": {"level": "warn"},
            "experimental": {
                "clash_api": {
                    "external_controller": f"127.0.0.1:{self.api_port}",
                    "secret": self.secret,
                }
            },
            "inbounds": inbounds,
            "outbounds": outbounds,
            "route": {"final": SELECTOR_TAG},
        }

    def write_config(self) -> Path:
        """Write the generated config to a secure temporary file."""
        payload = json.dumps(self.build_config(), indent=2)

        fd, name = tempfile.mkstemp(prefix="proxyrotate-", suffix=".json")
        self.config_file = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.chmod(name, 0o600)
        except BaseException:
            self._remove_config()
            raise
        return self.config_file

    def validate_config(self) -> None:
        """Validate the written config using `sing-box check`."""
        if self.config_file is None:
            raise SingBoxError("no config file written yet")

        result = subprocess.run(
            [self.binary, "check", "-c", str(self.config_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise SingBoxError(f"sing-box rejected the generated config:\n{detail}")

    # -- lifecycle -----------------------------------------------------------

    def start(self, timeout: float = _START_TIMEOUT) -> None:
        self.write_config()

        try:
            self.validate_config()
        except (OSError, subprocess.SubprocessError) as exc:
            raise SingBoxError(f"could not run sing-box check: {exc}") from exc

        self.log.info("starting %s (api port %d)", self.binary, self.api_port)

        # Kept open for the process lifetime so sing-box stderr can be
        # inspected on startup failure; unlinked in stop().
        self._stderr_file = tempfile.TemporaryFile(  # noqa: SIM115
            "w+", encoding="utf-8", errors="replace"
        )
        self._process = subprocess.Popen(
            [self.binary, "run", "-c", str(self.config_file)],
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_file,
            stdin=subprocess.DEVNULL,
        )

        try:
            self._wait_ready(timeout)
        except BaseException:
            self.stop()
            raise

        self.log.info("sing-box ready")

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise SingBoxError(
                    f"sing-box exited during startup "
                    f"(status {self._process.returncode}): {self._stderr_tail()}"
                )
            try:
                with self._api_get("/version", timeout=0.4) as response:
                    response.read(64)
                return
            except OSError:
                time.sleep(0.1)
        raise SingBoxError(
            f"sing-box API not ready within {timeout:.0f}s: {self._stderr_tail()}"
        )

    def stop(self) -> None:
        """Stop sing-box and remove all temporary files. Safe to call twice."""
        process = self._process
        self._process = None

        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=2)
            self.log.info("sing-box stopped")

        if self._stderr_file is not None:
            self._stderr_file.close()
            self._stderr_file = None

        self._remove_config()

    # -- clash api -----------------------------------------------------------

    def select(self, tag: str) -> None:
        """Switch the selector outbound to the given proxy tag."""
        request = urlrequest.Request(
            f"{self._api_base}/proxies/{SELECTOR_TAG}",
            data=json.dumps({"name": tag}).encode(),
            method="PUT",
            headers=self._api_headers(),
        )
        try:
            with urlrequest.urlopen(request, timeout=5) as response:
                response.read(64)
        except HTTPError as exc:
            detail = ""
            with contextlib.suppress(Exception):
                detail = exc.read(200).decode(errors="replace").strip()
            raise SingBoxError(f"switch to {tag} failed: HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise SingBoxError(f"switch to {tag} failed: {_brief(exc)}") from exc

    def connections_payload(self) -> dict:
        """Raw ``GET /connections`` payload (active connections + totals)."""
        request = urlrequest.Request(
            f"{self._api_base}/connections", headers=self._api_headers()
        )
        with urlrequest.urlopen(request, timeout=2) as response:
            return json.loads(response.read() or b"{}")

    def verify_current(self, timeout: float = 3.0) -> float:
        """Fetch a small URL through the local inbound.

        Proves the currently selected outbound works end-to-end.
        Returns latency in milliseconds; raises on failure.
        """
        opener = urlrequest.build_opener(
            urlrequest.ProxyHandler({"http": f"http://127.0.0.1:{self.inbound_port}"})
        )
        req = urlrequest.Request(DEFAULT_CHECK_URL, headers={"User-Agent": "proxyrotate"})
        start = time.monotonic()
        try:
            with opener.open(req, timeout=timeout) as response:
                response.read(64)
        except HTTPError:
            # Any well-formed HTTP reply proves the tunnel works.
            pass
        except (URLError, OSError) as exc:
            raise SingBoxError(_brief(exc)) from exc
        return (time.monotonic() - start) * 1000

    # -- helpers -------------------------------------------------------------

    def env_for_child(self) -> dict[str, str]:
        """Proxy environment variables pointing at the local inbound.

        These make the target application route traffic into sing-box
        regardless of whether it supports process-based routing.
        """
        url = f"http://127.0.0.1:{self.inbound_port}"
        no_proxy = "localhost,127.0.0.1,::1"
        env: dict[str, str] = {}
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            env[name] = url
            env[name.lower()] = url
        for name in ("NO_PROXY", "no_proxy"):
            env[name] = no_proxy
        return env

    @property
    def _api_base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    def _api_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secret}",
            "Content-Type": "application/json",
        }

    def _api_get(self, path: str, timeout: float):
        request = urlrequest.Request(
            f"{self._api_base}{path}", headers=self._api_headers()
        )
        return urlrequest.urlopen(request, timeout=timeout)

    def _stderr_tail(self) -> str:
        if self._stderr_file is None:
            return ""
        try:
            self._stderr_file.seek(0)
            text = self._stderr_file.read().strip()
        except (OSError, ValueError):
            return ""
        lines = text.splitlines()[-10:]
        return "\n".join(lines)

    def _remove_config(self) -> None:
        if self.config_file is not None:
            self.config_file.unlink(missing_ok=True)
            self.config_file = None


def _brief(exc: Exception) -> str:
    reason = getattr(exc, "reason", None)
    text = str(reason or exc).strip()
    if isinstance(reason, socket.timeout) or "timed out" in text:
        return "timeout"
    if "refused" in text:
        return "connection refused"
    return text[:80] or exc.__class__.__name__


@dataclass(slots=True)
class ActivityDelta:
    """Activity observed through the inbound since the last reset."""

    new_connections: int = 0
    new_bytes: int = 0


class ActivityMonitor:
    """Tracks traffic activity via the Clash API ``GET /connections``.

    Two counters are maintained since construction (or :meth:`reset`):

    ``new_connections``
        Distinct connection IDs seen. The endpoint lists *active*
        connections only, so a tunnel shorter than one poll interval may
        be missed; rotations can therefore trigger slightly late. This
        is an accepted approximation of "requests" — apps holding
        long-lived tunnels (browsers, API clients with keep-alive) are
        tracked reliably.

    ``new_bytes``
        Deltas of the cumulative ``downloadTotal + uploadTotal`` counters.
        Exact — closed connections still contribute to the totals —
        except that it includes the few hundred bytes of ProxyRotate's
        own verification requests.

    Connections created by ProxyRotate's own switch verification are
    excluded from ``new_connections``: they are recognised by the
    health-check host in the connection metadata (including the IP-only
    form, where the host was resolved before the tunnel was opened).
    """

    def __init__(self, manager, check_host: str | None = None,
                 clock=time.monotonic, poll_interval: float = 0.25,
                 resolver=socket.getaddrinfo):
        from .logging import get_logger

        self._manager = manager
        self._clock = clock
        self._poll_interval = poll_interval
        self._check_host = check_host or urlsplit(DEFAULT_CHECK_URL).hostname or ""
        self._resolver = resolver
        self._resolved_ips: frozenset[str] | None = None
        self._resolution_attempted = False
        self._seen: set[str] = set()
        self._last_totals: int | None = None
        self._next_poll_at = 0.0
        self._warned = False
        self._pending_connections = 0
        self._pending_bytes = 0
        self.log = get_logger("activity")

    @property
    def activity(self) -> ActivityDelta:
        return ActivityDelta(new_connections=self._pending_connections,
                             new_bytes=self._pending_bytes)

    def poll(self) -> None:
        """Refresh counters (rate-limited to once per ``poll_interval``)."""
        now = self._clock()
        if now < self._next_poll_at:
            return
        self._next_poll_at = now + self._poll_interval

        try:
            payload = self._manager.connections_payload()
        except Exception as exc:
            self._warn_tracking_unavailable(exc)
            return

        self._ingest(payload)

    def reset(self) -> None:
        """Zero pending counters and re-baseline against the current state.

        Called right after a successful switch (including its
        verification). A fresh ``/connections`` snapshot is taken so that

        * every connection still active from the previous round — the
          verification tunnel included — joins the seen set and can never
          be counted again, while IDs appearing afterwards count exactly
          once; and
        * the byte totals baseline moves to the value *now* on the wire,
          so bytes transferred before the reset (verification traffic
          included) cannot leak into the next round's ``new_bytes``.

        If the activity endpoint is unavailable the existing baselines
        are kept: they already exclude everything observed so far, and
        degraded-mode polling resumes without raising.
        """
        self._pending_connections = 0
        self._pending_bytes = 0
        try:
            payload = self._manager.connections_payload()
        except Exception as exc:
            self._warn_tracking_unavailable(exc)
            return
        self._ingest(payload, rebasing=True)

    # -- internals -----------------------------------------------------------

    def _ingest(self, payload: dict, rebasing: bool = False) -> None:
        current_ids: set[str] = set()
        for entry in payload.get("connections") or []:
            cid = entry.get("id")
            if cid is None:
                continue
            metadata = entry.get("metadata") or {}
            if self._is_health_check(metadata):
                continue  # our own switch-verification traffic
            current_ids.add(cid)
            if rebasing:
                # Re-aligning after a switch: connections active now
                # belong to the previous round and must never be counted,
                # but stay in the seen set so they cannot be counted later.
                self._seen.add(cid)
            elif cid not in self._seen:
                self._seen.add(cid)
                self._pending_connections += 1

        # Prune IDs that vanished so long sessions don't grow memory.
        self._seen &= current_ids

        totals = int(payload.get("downloadTotal") or 0) + int(payload.get("uploadTotal") or 0)
        if rebasing or self._last_totals is None:
            # Establish/move the baseline: totals observed up to now do
            # not count towards the next round's byte delta.
            self._last_totals = totals
        else:
            self._pending_bytes += max(0, totals - self._last_totals)
            self._last_totals = totals

    def _is_health_check(self, metadata: dict) -> bool:
        """Whether a /connections entry is ProxyRotate's own verification.

        Clash metadata usually carries the SNI/HTTP host in ``host``;
        connections whose host was resolved locally can instead show
        only an IP in ``destinationIP``. Both forms are recognised.
        """
        if not self._check_host:
            return False
        host = metadata.get("host") or metadata.get("sniffHost") or ""
        if host and host.rstrip(".").lower() == self._check_host.rstrip(".").lower():
            return True
        destination_ip = metadata.get("destinationIP")
        return bool(
            destination_ip and destination_ip in self._check_host_ips()
        )

    def _check_host_ips(self) -> frozenset[str]:
        # Resolved lazily (only when a metadata entry carries no host)
        # and cached, including the empty result: a failing DNS lookup
        # must not add a multi-second stall to every later poll.
        if not self._resolution_attempted:
            self._resolution_attempted = True
            self._resolved_ips = frozenset(self._resolve(self._check_host))
        return self._resolved_ips or frozenset()

    def _resolve(self, host: str) -> set[str]:
        try:
            infos = self._resolver(host, None)
        except OSError:
            return set()
        return {info[4][0] for info in infos}

    def _warn_tracking_unavailable(self, exc: Exception) -> None:
        if not self._warned:
            self._warned = True
            self.log.warning(
                "connection tracking unavailable (%s); "
                "request-based rotation degraded", _brief(exc)
            )
        else:
            self.log.debug("connections poll failed: %s", exc)
