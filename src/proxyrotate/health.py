"""Proxy health checking.

Each proxy is tested end-to-end by fetching a small URL through it:

  - http/https proxies are used via urllib's proxy support
  - socks5 proxies get a minimal SOCKS5 CONNECT handshake implemented on
    top of the standard library socket module (no external dependencies)

Checks run in a bounded worker pool so a large list never blocks the
program or spawns one thread per proxy.
"""

from __future__ import annotations

import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from .models import Proxy

DEFAULT_CHECK_URL = "http://www.gstatic.com/generate_204"
DEFAULT_MAX_WORKERS = 20


@dataclass(slots=True)
class HealthResult:
    proxy: Proxy
    ok: bool
    latency_ms: float | None = None
    error: str | None = None


def check_proxy(proxy: Proxy, timeout: float, url: str | None = None) -> HealthResult:
    """Check a single proxy. Never raises; failures become !ok results."""
    url = url or DEFAULT_CHECK_URL
    start = time.monotonic()
    try:
        if proxy.scheme == "socks5":
            _socks5_fetch(proxy, url, timeout)
        else:
            opener = urlrequest.build_opener(
                urlrequest.ProxyHandler({proxy.scheme: proxy.url()})
            )
            req = urlrequest.Request(url, headers={"User-Agent": "proxyrotate-health"})
            with opener.open(req, timeout=timeout) as response:
                response.read(64)
        latency_ms = (time.monotonic() - start) * 1000
        return HealthResult(proxy=proxy, ok=True, latency_ms=latency_ms)
    except HTTPError as exc:
        # Any well-formed HTTP response proves the tunnel works.
        latency_ms = (time.monotonic() - start) * 1000
        return HealthResult(proxy=proxy, ok=True, latency_ms=latency_ms,
                            error=f"http status {exc.code}")
    except (URLError, TimeoutError) as exc:
        return HealthResult(proxy=proxy, ok=False, error=_short_error(exc))
    except OSError as exc:
        reason = _short_error(exc)
        if "timed out" in reason:
            reason = "timeout"
        return HealthResult(proxy=proxy, ok=False, error=reason)


def check_proxies(
    proxies: list[Proxy],
    timeout: float,
    max_workers: int = DEFAULT_MAX_WORKERS,
    on_result=None,
    url: str | None = None,
) -> list[HealthResult]:
    """Check many proxies concurrently, preserving input order in the result."""
    if not proxies:
        return []

    url = url or DEFAULT_CHECK_URL
    workers = max(1, min(max_workers, len(proxies)))
    results: list[HealthResult | None] = [None] * len(proxies)

    def worker(index: int, proxy: Proxy) -> None:
        result = check_proxy(proxy, timeout, url)
        results[index] = result
        if on_result is not None:
            on_result(result)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, proxy in enumerate(proxies):
            pool.submit(worker, index, proxy)

    return [r for r in results if r is not None]


def healthy_only(results: list[HealthResult]) -> list[Proxy]:
    """Proxies from successful checks, fastest first."""
    passed = [r for r in results if r.ok and r.latency_ms is not None]
    passed.sort(key=lambda r: r.latency_ms or 0)
    return [r.proxy for r in passed]


def _short_error(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, URLError) and exc.reason is not None:
        text = str(exc.reason)
    text = text.strip()
    if "timed out" in text or isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc.__cause__, ConnectionRefusedError) or isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    if "refused" in text:
        return "connection refused"
    if "nodename nor servname" in text or "Name or service" in text or "getaddrinfo" in text:
        return "dns error"
    return text[:80] if text else exc.__class__.__name__


# --- minimal SOCKS5 client -------------------------------------------------


def _socks5_connect(proxy: Proxy, host: str, port: int, timeout: float) -> socket.socket:
    sock = socket.create_connection((proxy.host, proxy.port), timeout=timeout)

    try:
        methods = b"\x05\x01\x00" if proxy.username is None else b"\x05\x02\x00\x02"
        sock.sendall(methods)

        reply = _recv_exact(sock, 2)
        if reply[0] != 0x05:
            raise OSError("not a SOCKS5 server")
        method = reply[1]

        if method == 0x02:
            if proxy.username is None:
                raise OSError("server requires authentication")
            user = proxy.username.encode()
            password = (proxy.password or "").encode()
            sock.sendall(bytes([0x01, len(user)]) + user + bytes([len(password)]) + password)
            status = _recv_exact(sock, 2)[1]
            if status != 0x00:
                raise OSError("authentication failed")
        elif method != 0x00:
            raise OSError(f"server chose unsupported auth method {method}")

        host_bytes = host.encode("idna")
        request = (
            b"\x05\x01\x00\x03"
            + bytes([len(host_bytes)])
            + host_bytes
            + struct.pack("!H", port)
        )
        sock.sendall(request)

        head = _recv_exact(sock, 4)
        if head[0] != 0x05 or head[1] != 0x00:
            reasons = {
                0x01: "general failure",
                0x02: "not allowed",
                0x03: "network unreachable",
                0x04: "host unreachable",
                0x05: "connection refused",
                0x06: "ttl expired",
                0x07: "command not supported",
                0x08: "address type not supported",
            }
            raise OSError(f"connect failed: {reasons.get(head[1], f'code {head[1]}')}")

        atyp = head[3]
        if atyp == 0x01:
            _recv_exact(sock, 4 + 2)
        elif atyp == 0x03:
            length = _recv_exact(sock, 1)[0]
            _recv_exact(sock, length + 2)
        elif atyp == 0x04:
            _recv_exact(sock, 16 + 2)
        else:
            raise OSError(f"bad address type {atyp} in reply")

        return sock
    except BaseException:
        sock.close()
        raise


def _socks5_fetch(proxy: Proxy, url: str, timeout: float) -> None:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    host = parts.hostname or ""

    with _socks5_connect(proxy, host, port, timeout) as sock:
        path = parts.path or "/"
        request_text = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: proxyrotate-health\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        sock.sendall(request_text.encode())

        reader = sock.makefile("rb")
        try:
            status_line = reader.readline(1024).decode("latin-1").strip()
        finally:
            reader.close()

        if not status_line.startswith("HTTP/"):
            raise OSError(f"bad response: {status_line[:40]!r}")


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError("connection closed during handshake")
        data.extend(chunk)
    return bytes(data)
