import json
import socket
import stat
from urllib.error import HTTPError, URLError

import pytest

import proxyrotate.singbox as singbox_module
from proxyrotate.models import Proxy
from proxyrotate.singbox import (
    SELECTOR_TAG,
    SingBoxError,
    SingBoxManager,
    discover_binary,
)


def proxies(*urls):
    return [Proxy.parse(url) for url in urls]


@pytest.fixture
def manager(tmp_path):
    return SingBoxManager(
        proxies("http://1.1.1.1:8080", "socks5://bob:pw@2.2.2.2:1080"),
        binary="/fake/sing-box",
    )


class TestBuildConfig:
    def test_structure(self, manager):
        config = manager.build_config()

        assert config["route"] == {"final": SELECTOR_TAG}

        clash = config["experimental"]["clash_api"]
        assert clash["external_controller"].startswith("127.0.0.1:")
        assert clash["secret"]

        (inbound,) = config["inbounds"]
        assert inbound["type"] == "mixed"
        assert inbound["listen"] == "127.0.0.1"

    def test_outbounds(self, manager):
        outbounds = {o["tag"]: o for o in manager.build_config()["outbounds"]}

        http_out = outbounds["proxy-0"]
        assert http_out["type"] == "http"
        assert http_out["server"] == "1.1.1.1"
        assert http_out["server_port"] == 8080

        socks_out = outbounds["proxy-1"]
        assert socks_out["type"] == "socks"
        assert socks_out["version"] == "5"
        assert socks_out["username"] == "bob"
        assert socks_out["password"] == "pw"  # required by sing-box; never logged

        selector = outbounds[SELECTOR_TAG]
        assert selector["type"] == "selector"
        assert selector["outbounds"] == ["proxy-0", "proxy-1"]
        assert selector["default"] == "proxy-0"

        assert outbounds["direct"]["type"] == "direct"

    def test_unique_ports_and_secret(self):
        first = SingBoxManager(proxies("http://h:1"), binary="x")
        second = SingBoxManager(proxies("http://h:1"), binary="x")
        assert first.api_port != second.api_port or first.inbound_port != second.inbound_port
        assert first.secret != second.secret


class TestWriteConfig:
    def test_written_with_secure_permissions(self, manager):
        path = manager.write_config()
        try:
            data = json.loads(path.read_text())
            assert len(data["outbounds"]) == 4  # 2 proxies + selector + direct
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o600
        finally:
            manager.stop()
        assert not path.exists()

    def test_stop_is_idempotent(self, manager):
        manager.write_config()
        manager.stop()
        manager.stop()  # must not raise


class TestDiscovery:
    def test_explicit_path(self, tmp_path):
        binary = tmp_path / "sing-box"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        assert discover_binary(str(binary)) == str(binary)

    def test_explicit_missing(self):
        with pytest.raises(SingBoxError, match="not found at"):
            discover_binary("/no/such/place")

    def test_explicit_not_executable(self, tmp_path):
        binary = tmp_path / "sing-box"
        binary.write_text("data")
        binary.chmod(0o644)
        with pytest.raises(SingBoxError):
            discover_binary(str(binary))

    def test_falls_back_to_path(self, monkeypatch):
        monkeypatch.setattr(singbox_module.shutil, "which", lambda name: "/usr/bin/sing-box")
        assert discover_binary(None) == "/usr/bin/sing-box"

    def test_common_location(self, tmp_path, monkeypatch):
        binary = tmp_path / "sb"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setattr(singbox_module.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            singbox_module, "_COMMON_BIN_LOCATIONS", (str(binary),)
        )
        assert discover_binary(None) == str(binary)

    def test_error_message_mentions_flag(self):
        with pytest.raises(SingBoxError, match="--sing-box"):
            discover_binary("/definitely/missing")


class ScriptedConnectionsManager:
    """Returns canned /connections payloads."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def connections_payload(self) -> dict:
        self.calls += 1
        if self.payloads:
            item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def conn(cid, host="api.example.com", **metadata):
    meta = {"host": host}
    meta.update(metadata)
    return {"id": cid, "metadata": meta, "upload": 0, "download": 0}


def fake_resolver(mapping):
    """Build a getaddrinfo-style resolver from a host -> [ips] map."""

    def resolve(host, port):
        if host not in mapping:
            raise socket.gaierror(f"no address for {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
                for ip in mapping[host]]

    return resolve


class TestActivityMonitor:
    def make(self, payloads, resolver=None):
        import proxyrotate.singbox as sb

        state = {"now": 100.0}

        def clock():
            return state["now"]

        def advance(seconds):
            state["now"] += seconds

        mgr = ScriptedConnectionsManager(payloads)
        monitor = sb.ActivityMonitor(
            mgr, check_host="www.gstatic.com", clock=clock,
            resolver=resolver or fake_resolver(
                {"www.gstatic.com": ["203.0.113.7"]}
            ),
        )
        return monitor, mgr, advance

    def test_counts_new_connection_ids_once(self):
        monitor, mgr, advance = self.make([
            {"connections": [conn("a")], "downloadTotal": 0, "uploadTotal": 0},
            {"connections": [conn("a"), conn("b")], "downloadTotal": 0, "uploadTotal": 0},
        ])
        monitor.poll()
        assert monitor.activity.new_connections == 1
        advance(1)
        monitor.poll()
        assert monitor.activity.new_connections == 2   # only b is new

    def test_bytes_accumulate_from_totals_delta(self):
        monitor, mgr, advance = self.make([
            {"connections": [], "downloadTotal": 100, "uploadTotal": 0},
            {"connections": [], "downloadTotal": 450, "uploadTotal": 50},
            {"connections": [], "downloadTotal": 650, "uploadTotal": 50},
        ])
        monitor.poll()
        assert monitor.activity.new_bytes == 0          # baseline established
        advance(2)
        monitor.poll()
        assert monitor.activity.new_bytes == 400
        advance(2)
        monitor.poll()
        assert monitor.activity.new_bytes == 600

    def test_check_host_connections_ignored(self):
        monitor, mgr, advance = self.make([
            {"connections": [conn("v", "www.gstatic.com"), conn("a")],
             "downloadTotal": 0, "uploadTotal": 0},
            {"connections": [conn("a")], "downloadTotal": 500, "uploadTotal": 0},
        ])
        monitor.poll()
        assert monitor.activity.new_connections == 1    # v filtered
        advance(2)
        monitor.poll()
        # verify traffic bytes remain in global totals (documented, tiny)
        assert monitor.activity.new_bytes == 500

    def test_verification_connection_by_destination_ip_ignored(self):
        # sing-box may report the resolved IP instead of the host when
        # the local client (our verifier) resolved the name itself.
        monitor, mgr, advance = self.make([
            {"connections": [
                conn("v", host="", destinationIP="203.0.113.7"),
                conn("a"),
             ],
             "downloadTotal": 0, "uploadTotal": 0},
            {"connections": [
                conn("v2", host="", destinationIP="203.0.113.7"),
                conn("a"),
             ],
             "downloadTotal": 0, "uploadTotal": 0},
        ])
        monitor.poll()
        assert monitor.activity.new_connections == 1     # only a counted
        advance(1)
        monitor.poll()
        assert monitor.activity.new_connections == 1     # v2 filtered too

    def test_verification_connection_by_sniff_host_ignored(self):
        monitor, mgr, advance = self.make([
            {"connections": [
                conn("v", host="", sniffHost="www.gstatic.com"),
                conn("a"),
             ],
             "downloadTotal": 0, "uploadTotal": 0},
        ])
        monitor.poll()
        assert monitor.activity.new_connections == 1

    def test_unrelated_destination_ip_is_counted(self):
        monitor, mgr, advance = self.make([
            {"connections": [
                conn("x", host="", destinationIP="198.51.100.9"),
             ],
             "downloadTotal": 0, "uploadTotal": 0},
        ])
        monitor.poll()
        assert monitor.activity.new_connections == 1

    def test_dns_failure_does_not_block_or_crash(self):
        resolver = fake_resolver({})  # nothing resolvable
        monitor, mgr, advance = self.make([
            {"connections": [conn("x", host="", destinationIP="203.0.113.7")]},
            {"connections": [conn("x", host="", destinationIP="203.0.113.7")]},
        ], resolver=resolver)
        monitor.poll()
        advance(1)
        monitor.poll()  # no retry storm: failed lookup is cached empty
        assert mgr.calls == 2
        # Without a usable IP match the connection simply counts normally.
        assert monitor.activity.new_connections == 1

    def test_reset_rebases_seen_set_against_live_connections(self):
        # First round: one target connection and one verification tunnel
        # still active at reset time.
        monitor, mgr, advance = self.make([
            {"connections": [conn("a")], "downloadTotal": 100, "uploadTotal": 0},
            # reset() snapshot: "a" still open plus the verifier tunnel
            {"connections": [conn("a"),
                             conn("v", host="www.gstatic.com")],
             "downloadTotal": 700, "uploadTotal": 0},
            # next poll: a and v remain, a brand-new target tunnel appears
            {"connections": [conn("a"),
                             conn("v", host="www.gstatic.com"),
                             conn("b")],
             "downloadTotal": 900, "uploadTotal": 0},
        ])
        monitor.poll()
        assert monitor.activity.new_connections == 1
        advance(1)
        monitor.reset()
        assert monitor.activity.new_connections == 0
        assert monitor.activity.new_bytes == 0
        advance(1)
        monitor.poll()
        # "a" predates the reset and must not be double-counted; the
        # verification tunnel never counts; only "b" is new.
        assert monitor.activity.new_connections == 1
        # byte baseline moved to 700 at reset: only the 200 after counts.
        assert monitor.activity.new_bytes == 200

    def test_reset_baseline_excludes_pre_reset_bytes(self):
        monitor, mgr, advance = self.make([
            {"connections": [], "downloadTotal": 1000, "uploadTotal": 0},
            {"connections": [], "downloadTotal": 2500, "uploadTotal": 0},
            {"connections": [], "downloadTotal": 2600, "uploadTotal": 0},
        ])
        monitor.poll()
        advance(1)
        monitor.reset()                              # rebased at 2500
        assert monitor.activity.new_bytes == 0
        advance(1)
        monitor.poll()
        assert monitor.activity.new_bytes == 100

    def test_reset_with_unavailable_endpoint_keeps_baselines(self):
        monitor, mgr, advance = self.make([
            {"connections": [conn("a")], "downloadTotal": 100, "uploadTotal": 0},
            RuntimeError("api down"),
            {"connections": [conn("a"), conn("b")],
             "downloadTotal": 300, "uploadTotal": 0},
        ])
        monitor.poll()
        advance(1)
        monitor.reset()                              # warns, does not raise
        assert monitor.activity.new_connections == 0
        assert monitor.activity.new_bytes == 0
        advance(1)
        monitor.poll()
        # Old baseline still valid: "a" was already seen, "b" is new;
        # byte delta is measured from the last successful total (100).
        assert monitor.activity.new_connections == 1
        assert monitor.activity.new_bytes == 200

    def test_reset_zeroes_and_rebases(self):
        monitor, mgr, advance = self.make([
            {"connections": [conn("a")], "downloadTotal": 1000, "uploadTotal": 0},
            {"connections": [conn("a")], "downloadTotal": 1500, "uploadTotal": 0},
        ])
        monitor.poll()
        advance(2)
        monitor.poll()
        assert monitor.activity.new_bytes == 500
        monitor.reset()
        assert monitor.activity.new_bytes == 0
        assert monitor.activity.new_connections == 0
        advance(2)
        monitor.poll()                                  # a stays active: not recounted
        assert monitor.activity.new_connections == 0
        assert monitor.activity.new_bytes == 0          # rebased at 1500

    def test_rate_limited_polls(self):
        monitor, mgr, advance = self.make([
            {"connections": [], "downloadTotal": 0, "uploadTotal": 0},
        ])
        monitor.poll()
        monitor.poll()                                  # same instant -> cached
        assert mgr.calls == 1
        advance(1)
        monitor.poll()
        assert mgr.calls == 2

    def test_stale_ids_pruned_no_memory_growth(self):
        monitor, mgr, advance = self.make([
            {"connections": [conn("old")], "downloadTotal": 0, "uploadTotal": 0},
            {"connections": [], "downloadTotal": 0, "uploadTotal": 0},
            {"connections": [conn("old")], "downloadTotal": 0, "uploadTotal": 0},
        ])
        monitor.poll()
        advance(1)
        monitor.poll()                                  # old vanished -> pruned
        advance(1)
        monitor.poll()
        assert monitor.activity.new_connections == 2    # pruned id counts again if ever reseen

    def test_endpoint_failure_degrades_gracefully(self):
        monitor, mgr, advance = self.make([
            RuntimeError("no /connections endpoint"),
            {"connections": [conn("a")], "downloadTotal": 5, "uploadTotal": 5},
        ])
        monitor.poll()                                  # must not raise
        assert monitor.activity.new_connections == 0
        advance(1)
        monitor.poll()                                  # recovers afterwards
        assert monitor.activity.new_connections == 1


class FakeResponse:
    def __init__(self, body=b"{}"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount=-1):
        return self._body


class FakeOpener:
    def __init__(self, exc=None):
        self.exc = exc
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.exc is not None:
            raise self.exc
        return FakeResponse()


class TestClashApi:
    def test_select_sends_put(self, manager, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.method
            captured["body"] = request.data
            captured["headers"] = dict(request.header_items())
            return FakeResponse()

        monkeypatch.setattr(singbox_module.urlrequest, "urlopen", fake_urlopen)
        manager.select("proxy-1")

        assert captured["method"] == "PUT"
        assert captured["url"].endswith(f"/proxies/{SELECTOR_TAG}")
        assert json.loads(captured["body"]) == {"name": "proxy-1"}
        auth = captured["headers"].get("Authorization", "")
        assert auth == f"Bearer {manager.secret}"

    def test_select_http_error_wrapped(self, manager, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise HTTPError("u", 400, "bad request", None, None)

        monkeypatch.setattr(singbox_module.urlrequest, "urlopen", fake_urlopen)
        with pytest.raises(SingBoxError, match="switch to proxy-9 failed"):
            manager.select("proxy-9")

    def test_verify_current_returns_latency(self, manager, monkeypatch):
        captured_handlers = []

        def fake_build_opener(handler):
            captured_handlers.append(handler)

            class Opener:
                def open(self, request, timeout=None):
                    return FakeResponse()

            return Opener()

        monkeypatch.setattr(singbox_module.urlrequest, "build_opener", fake_build_opener)
        latency = manager.verify_current(timeout=2)

        assert isinstance(latency, float)
        assert latency >= 0
        # The check must be routed through the local mixed inbound.
        (proxy_handler,) = captured_handlers
        inbound_url = f"http://127.0.0.1:{manager.inbound_port}"
        assert proxy_handler.proxies == {"http": inbound_url}

    def test_verify_failure_becomes_exception(self, manager, monkeypatch):
        opener = FakeOpener(exc=URLError("connection refused"))

        def build_opener(handler):
            return opener

        monkeypatch.setattr(singbox_module.urlrequest, "build_opener", build_opener)
        with pytest.raises(SingBoxError):
            manager.verify_current()


class TestEnvForChild:
    def test_points_at_local_inbound(self, manager):
        env = manager.env_for_child()
        expected = f"http://127.0.0.1:{manager.inbound_port}"
        assert env["HTTP_PROXY"] == expected
        assert env["HTTPS_PROXY"] == expected
        assert env["ALL_PROXY"] == expected
        assert env["NO_PROXY"] == "localhost,127.0.0.1,::1"
        assert env["http_proxy"] == expected


class TestStartupFailure:
    def test_dead_process_reported_with_stderr_tail(self, manager, monkeypatch, tmp_path):
        import tempfile as tempfile_mod

        class DeadProcess:
            returncode = 23

            def poll(self):
                return 23

        manager._process = DeadProcess()
        manager._stderr_file = tempfile_mod.TemporaryFile(  # noqa: SIM115
            "w+", encoding="utf-8"
        )

        def fail_api(request, timeout=None):
            raise URLError("refused")

        monkeypatch.setattr(singbox_module.urlrequest, "urlopen", fail_api)

        with pytest.raises(SingBoxError, match="exited during startup"):
            manager._wait_ready(timeout=0.5)
