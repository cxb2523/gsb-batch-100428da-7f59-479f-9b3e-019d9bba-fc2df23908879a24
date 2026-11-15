"""Local DNS relay for process routing.

nftables redirects the routed cgroup's destination-53 UDP traffic to
this relay. Every query is re-sent as DNS-over-TCP (RFC 1035 §4.2.2
framing) through sing-box's SOCKS inbound, so resolution always leaves
through the currently selected rotating proxy — never via the host
resolver.

This closes the classic DNS leak for every kind of client (glibc based
tools included): the app still talks plain UDP to what it believes is
its nameserver, conntrack restores the original source address in the
answer, and the query itself travels through the proxy. Nothing here
needs privileges; failures only drop the query (fail-closed) and the
client retries per its own policy.
"""

from __future__ import annotations

import socket
import struct
import threading
from concurrent.futures import ThreadPoolExecutor

from .logging import get_logger
from .models import Proxy


class RelayError(RuntimeError):
    """Raised when the relay cannot bind or run."""


class DnsRelay:
    """Answers redirected UDP DNS queries over TCP via the proxy chain."""

    def __init__(self, *, inbound_port: int,
                 resolver: tuple[str, int] = ("1.1.1.1", 53),
                 port: int, host: str = "127.0.0.1",
                 query_timeout: float = 4.0,
                 max_workers: int = 8, logger=None):
        self._inbound = ("127.0.0.1", inbound_port)
        self._resolver = resolver
        self._bound = (host, port)
        self._query_timeout = query_timeout
        self._max_workers = max_workers
        self.log = logger or get_logger("dnsrelay")

        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._stop = threading.Event()

    @property
    def port(self) -> int:
        return self._bound[1]

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(self._bound)
        except OSError as exc:
            sock.close()
            raise RelayError(f"cannot bind dns relay {self._bound}: {exc}") from exc
        sock.settimeout(0.2)
        self._socket = sock
        self._stop.clear()
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="dnsrelay"
        )
        self._thread = threading.Thread(
            target=self._serve, name="dnsrelay", daemon=True
        )
        self._thread.start()
        self.log.debug("dns relay listening on udp %s", self._bound)

    def stop(self) -> None:
        """Stop the relay; idempotent and safe before :meth:`start`."""
        self._stop.set()
        sock = self._socket
        self._socket = None
        if sock is not None:
            sock.close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        pool = self._pool
        self._pool = None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        self.log.debug("dns relay stopped")

    # -- serving -------------------------------------------------------------

    def _serve(self) -> None:
        sock = self._socket
        while sock is not None and not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:  # socket.timeout alias; recv timed out
                continue
            except OSError:
                break
            if not data:
                continue
            pool = self._pool
            if pool is None:
                break
            pool.submit(self._answer, sock, data, addr)

    def _answer(self, sock: socket.socket, query: bytes, addr) -> None:
        """Send one framed DNS query through the inbound, relay the reply."""
        host, port = self._resolver
        try:
            reply = _query_via_socks(
                self._inbound,
                target=(host, port),
                payload=query,
                timeout=self._query_timeout,
            )
        except OSError as exc:
            self.log.debug("dns query (%d bytes) failed: %s", len(query), exc)
            return
        if reply:
            try:
                sock.sendto(reply, addr)
            except OSError as exc:
                self.log.debug("dns reply delivery failed: %s", exc)


def _query_via_socks(inbound: tuple[str, int],
                     target: tuple[str, int],
                     payload: bytes,
                     timeout: float) -> bytes:
    """One DNS-over-TCP round trip through sing-box's SOCKS inbound."""
    from .health import _socks5_connect  # reuse the audited handshake

    proxy = Proxy(scheme="socks5", host=inbound[0], port=inbound[1])
    stream = _socks5_connect(proxy, target[0], target[1], timeout)
    try:
        stream.settimeout(timeout)
        stream.sendall(struct.pack("!H", len(payload)) + payload)
        head = _recv_exact(stream, 2)
        (size,) = struct.unpack("!H", head)
        return _recv_exact(stream, size)
    finally:
        stream.close()


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError("connection closed during dns exchange")
        data.extend(chunk)
    return bytes(data)
