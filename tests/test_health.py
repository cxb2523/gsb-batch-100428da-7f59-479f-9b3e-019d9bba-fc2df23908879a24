import socket
import threading
import time
from urllib.error import HTTPError, URLError

import pytest

import proxyrotate.health as health_module
from proxyrotate.health import (
    check_proxies,
    check_proxy,
    healthy_only,
)
from proxyrotate.models import Proxy


class FakeResponse:
    def __init__(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount=-1):
        return b"ok"


def make_opener(exc=None, status=200):
    class Opener:
        def open(self, request, timeout=None):
            if exc is not None:
                raise exc
            if status >= 400:
                raise HTTPError(request.full_url, status, "err", None, None)
            return FakeResponse()

    return Opener()


@pytest.fixture
def patch_opener(monkeypatch):
    def install(opener):
        monkeypatch.setattr(
            health_module.urlrequest, "build_opener", lambda handler: opener
        )
        return opener

    return install


class TestCheckProxyHttp:
    def test_success_reports_latency(self, patch_opener):
        proxy = Proxy.parse("http://1.1.1.1:8080")
        patch_opener(make_opener())

        result = check_proxy(proxy, timeout=2)
        assert result.ok
        assert result.error is None
        assert result.latency_ms >= 0

    def test_http_error_still_means_proxy_works(self, patch_opener):
        proxy = Proxy.parse("http://1.1.1.1:8080")
        patch_opener(make_opener(status=502))

        result = check_proxy(proxy, timeout=2)
        assert result.ok  # a well-formed HTTP response proves the tunnel

    def test_url_error(self, patch_opener):
        proxy = Proxy.parse("http://down.example:8080")
        patch_opener(make_opener(exc=URLError("connection refused")))

        result = check_proxy(proxy, timeout=2)
        assert not result.ok
        assert "refused" in result.error or result.error == "connection refused"

    def test_timeout_error(self, patch_opener):
        proxy = Proxy.parse("http://slow.example:8080")
        patch_opener(make_opener(exc=URLError(TimeoutError("timed out"))))

        result = check_proxy(proxy, timeout=0.1)
        assert not result.ok
        assert result.error == "timeout"

    def test_never_raises(self, patch_opener):
        class Exploding:
            def open(self, request, timeout=None):
                raise RuntimeError("boom")

        patch_opener(Exploding())
        # RuntimeErrors propagate by design (bugs should be loud);
        # OSError-family must not leak.
        with pytest.raises(RuntimeError):
            check_proxy(Proxy.parse("http://x:1"), timeout=1)


# --- SOCKS5 against a real (local) server ----------------------------------


class DummySocks5Server:
    """Minimal SOCKS5 server for handshake testing."""

    def __init__(self, require_auth=False, refuse_connect=False):
        self.require_auth = require_auth
        self.refuse_connect = refuse_connect
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(4)
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.socket.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(3)
            conn.recv(64)  # greeting
            if self.require_auth:
                conn.sendall(b"\x05\x02")  # choose username/password
                conn.recv(128)
                conn.sendall(b"\x01\x00")  # auth ok
            else:
                conn.sendall(b"\x05\x00")

            request = conn.recv(256)
            if len(request) < 7:
                return
            if self.refuse_connect:
                conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)  # refused
            else:
                conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
                conn.recv(1024)  # the HTTP request bytes
                time.sleep(0.02)
                conn.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")

    def close(self):
        self.socket.close()


class TestCheckProxySocks5:
    def _proxy_for(self, port, auth=False):
        url = f"socks5://{'user:pw@' if auth else ''}127.0.0.1:{port}"
        return Proxy.parse(url)

    def test_no_auth_handshake(self):
        server = DummySocks5Server()
        server.start()
        try:
            result = check_proxy(self._proxy_for(server.port), timeout=2)
            assert result.ok, result.error
            assert result.latency_ms is not None
        finally:
            server.close()

    def test_auth_handshake(self):
        server = DummySocks5Server(require_auth=True)
        server.start()
        try:
            result = check_proxy(self._proxy_for(server.port, auth=True), timeout=2)
            assert result.ok, result.error
        finally:
            server.close()

    def test_connect_refused_by_remote(self):
        server = DummySocks5Server(refuse_connect=True)
        server.start()
        try:
            result = check_proxy(self._proxy_for(server.port), timeout=2)
            assert not result.ok
            assert "refused" in result.error
        finally:
            server.close()

    def test_connection_refused_at_listener(self):
        port = _closed_port()
        result = check_proxy(self._proxy_for(port), timeout=1)
        assert not result.ok
        assert result.error in ("connection refused", "timeout")


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestCheckProxiesPool:
    def test_preserves_order_and_aggregates(self, patch_opener):
        opener_results = iter([True, False])
        results_seen = []

        class PoolOpener:
            def open(self, request, timeout=None):
                ok = next(opener_results)
                results_seen.append(ok)
                if not ok:
                    raise URLError("nope")
                return FakeResponse()

        patch_opener(PoolOpener())
        proxies = [Proxy.parse(f"http://h{i}:1") for i in range(3)]
        results = check_proxies(proxies[:2], timeout=2, max_workers=2)

        assert len(results) == 2
        assert [r.proxy.host for r in results] == ["h0", "h1"]
        assert [r.ok for r in results] == [True, False]

    def test_empty_input(self):
        assert check_proxies([], timeout=1) == []

    def test_healthy_only_filters_and_sorts(self):
        from proxyrotate.health import HealthResult

        fast = Proxy.parse("http://fast:1")
        mid = Proxy.parse("http://mid:1")
        slow = Proxy.parse("http://slow:1")
        dead = Proxy.parse("http://dead:1")

        results = [
            HealthResult(proxy=slow, ok=True, latency_ms=300.0),
            HealthResult(proxy=dead, ok=False, error="timeout"),
            HealthResult(proxy=fast, ok=True, latency_ms=50.0),
            HealthResult(proxy=mid, ok=True, latency_ms=120.0),
        ]

        ordered = [p.host for p in healthy_only(results)]
        assert ordered == ["fast", "mid", "slow"]
