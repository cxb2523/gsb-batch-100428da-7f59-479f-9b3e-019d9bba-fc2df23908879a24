"""DNS relay tests.

Uses an in-test SOCKS5 stub that stands in for sing-box's mixed inbound
and answers framed DNS-over-TCP. Everything is local sockets and offline.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from proxyrotate.dnsrelay import DnsRelay, RelayError

A_ANSWER = bytes([198, 51, 100, 7])  # TEST-NET-2; never talked to for real


def _qname(name: str) -> bytes:
    encoded = b"".join(
        bytes([len(label)]) + label.encode() for label in name.split(".")
    )
    return encoded + b"\x00"


def _dns_query(tid: int = 0x5A5A, name: str = "example.org") -> bytes:
    """A minimal A-question payload, as any resolver would send."""
    header = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)  # RD=1
    return header + _qname(name) + struct.pack("!HH", 1, 1)


def _dns_reply(tid: int = 0x5A5A, name: str = "example.org") -> bytes:
    """Answer body the stub hands back: question echoed + one A record."""
    head = struct.pack("!HHHHHH", tid, 0x8180, 1, 1, 0, 0)  # QR|RA, an=1
    question = _qname(name) + struct.pack("!HH", 1, 1)
    record = (
        struct.pack("!HHIH", 0xC00C, 1, 1, 300) + struct.pack("!H", 4) + A_ANSWER
    )
    return head + question + record


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class SocksStub(threading.Thread):
    """Fake sing-box inbound: SOCKS5 CONNECT, then framed DNS-over-TCP.

    ``mode`` selects the failure behavior exercised by the fail-closed
    tests: ``serve`` (default) answers, ``refuse`` closes the tunnel
    before the handshake completes, ``silent`` completes the handshake
    and then never replies, and ``break_first`` serves normally after a
    first refused connection.
    """

    def __init__(self, reply: bytes, *, mode: str = "serve"):
        super().__init__(daemon=True)
        self.reply = reply
        self.mode = mode
        self.host, self.port = "127.0.0.1", 0
        self.queries: list[bytes] = []
        self.targets: list[bytes] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._connections = 0

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((self.host, self.port))
        listener.listen(8)
        listener.settimeout(0.1)
        self._listener = listener
        self.port = int(listener.getsockname()[1])
        while not self._stop.is_set():
            try:
                conn, _addr = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            thread = threading.Thread(
                target=self._handle, args=(conn,), daemon=True
            )
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.close()  # unblocks accept()

    # -- connection handling ---------------------------------------------------

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(3.0)
            with self._lock:
                broken = self.mode == "refuse" or (
                    self.mode == "break_first" and self._connections == 0
                )
                self._connections += 1
            if broken:
                return  # with-block drops the connection immediately

            if conn.recv(3) != b"\x05\x01\x00":  # no-auth greeting
                return
            conn.sendall(b"\x05\x00")
            with self._lock:
                self.targets.append(conn.recv(256))  # CONNECT request
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)  # success reply

            head = self._recv(conn, 2)
            (size,) = struct.unpack("!H", head)
            query = self._recv(conn, size)
            with self._lock:
                self.queries.append(query)

            if self.mode == "silent":
                time.sleep(60)  # never answer; the client times out
                return
            conn.sendall(struct.pack("!H", len(self.reply)) + self.reply)
        except OSError:  # timeouts and late reader-side closes
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _recv(self, conn: socket.socket, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = conn.recv(count - len(data))
            if not chunk:
                raise OSError("stub connection closed")
            data.extend(chunk)
        return bytes(data)


@pytest.fixture
def stub():
    server = SocksStub(_dns_reply())
    server.start()
    # wait until the listener reports its port
    deadline = time.monotonic() + 2.0
    while server.port == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    yield server
    server.stop()


def _make_relay(stub, *, port=None, query_timeout=2.0) -> DnsRelay:
    return DnsRelay(
        inbound_port=stub.port,
        resolver=("dns.test", 53),
        port=port if port is not None else _free_udp_port(),
        query_timeout=query_timeout,
    )


def _exchange(port: int, query: bytes, timeout: float = 2.0) -> bytes | None:
    """One client-style UDP round trip; None when nothing came back."""
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.settimeout(timeout)
        client.sendto(query, ("127.0.0.1", port))
        try:
            data, _addr = client.recvfrom(4096)
        except TimeoutError:
            return None
        return data
    finally:
        client.close()


class TestLifecycle:
    def test_start_stop_is_listening(self, stub):
        relay = _make_relay(stub)
        relay.start()
        try:
            assert relay.port > 0
            # The bound socket exists; a datagram is accepted without error.
            ping = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                ping.sendto(b"x", ("127.0.0.1", relay.port))
            finally:
                ping.close()
        finally:
            relay.stop()
        assert relay._thread is None
        assert relay._socket is None

    def test_stop_is_safe_before_start(self, stub):
        relay = _make_relay(stub)
        relay.stop()  # must not raise
        assert relay._thread is None

    def test_stop_is_idempotent(self, stub):
        relay = _make_relay(stub)
        relay.start()
        relay.stop()
        relay.stop()

    def test_restart_after_stop(self, stub):
        relay = _make_relay(stub)
        relay.start()
        relay.stop()
        relay.start()
        try:
            assert _exchange(relay.port, _dns_query()) == _dns_reply()
        finally:
            relay.stop()


class TestForwarding:
    def test_udp_query_is_framed_replied(self, stub):
        relay = _make_relay(stub)
        relay.start()
        try:
            query = _dns_query()
            assert _exchange(relay.port, query) == _dns_reply()
            deadline = time.monotonic() + 2.0
            while not stub.queries and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            relay.stop()

        # Exactly one query reached the stub, framing stripped, and the
        # CONNECT request targeted the configured resolver with its port.
        assert stub.queries == [query]
        connect = stub.targets[0]
        assert connect[:4] == b"\x05\x01\x00\x03"
        expected_host = bytes([len("dns.test")]) + b"dns.test"
        assert connect[4:4 + len(expected_host)] == expected_host
        assert connect[-2:] == struct.pack("!H", 53)

    def test_distinct_queries_each_get_their_answer(self, stub):
        relay = _make_relay(stub)
        relay.start()
        try:
            # The stub always answers with its one canned reply (tid
            # 0x5A5A); the relay is an opaque byte pipe either way.
            first = _dns_query(0x1111)
            second = _dns_query(0x2222)
            assert _exchange(relay.port, first) == _dns_reply()
            assert _exchange(relay.port, second) == _dns_reply()
        finally:
            relay.stop()
        assert len(stub.queries) == 2
        assert {query[0:2] for query in stub.queries} \
            == {b"\x11\x11", b"\x22\x22"}


class TestFailClosed:
    def test_tunnel_refused_drops_query(self):
        stub = SocksStub(_dns_reply(), mode="refuse")
        stub.start()
        try:
            relay = _make_relay(stub)
            relay.start()
            try:
                query = _dns_query()
                assert _exchange(relay.port, query, timeout=0.4) is None
                assert stub.queries == []
            finally:
                relay.stop()
        finally:
            stub.stop()

    def test_silent_tunnel_times_out_and_drops(self):
        stub = SocksStub(_dns_reply(), mode="silent")
        stub.start()
        deadline = time.monotonic() + 2.0
        while stub.port == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            relay = _make_relay(stub, query_timeout=0.3)
            relay.start()
            try:
                query = _dns_query()
                # No server reply until the relay's short timeout wins.
                assert _exchange(relay.port, query, timeout=1.5) is None
                q_deadline = time.monotonic() + 2.0
                while not stub.queries and time.monotonic() < q_deadline:
                    time.sleep(0.02)
                assert stub.queries == [query]  # received, never answered
            finally:
                relay.stop()
        finally:
            stub.stop()

    def test_recovery_after_failure(self):
        stub = SocksStub(_dns_reply(), mode="break_first")
        stub.start()
        deadline = time.monotonic() + 2.0
        while stub.port == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            relay = _make_relay(stub)
            relay.start()
            try:
                query = _dns_query()
                # First tunnel is refused -> dropped, nothing to the client.
                assert _exchange(relay.port, query, timeout=0.4) is None
                # The relay stays alive; the next one is answered.
                assert _exchange(relay.port, query) == _dns_reply()
            finally:
                relay.stop()
        finally:
            stub.stop()


class TestBindFailure:
    def test_occupied_port_raises_relay_error(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", 0))
        occupied = int(blocker.getsockname()[1])
        try:
            relay = DnsRelay(
                inbound_port=1, resolver=("dns.test", 53), port=occupied
            )
            with pytest.raises(RelayError) as excinfo:
                relay.start()
            assert "cannot bind" in str(excinfo.value)
            assert str(occupied) in str(excinfo.value)
            # A failed start leaves nothing behind.
            relay.stop()
            assert relay._thread is None
        finally:
            blocker.close()
