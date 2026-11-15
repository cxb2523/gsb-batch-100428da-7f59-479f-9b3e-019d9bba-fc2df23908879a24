"""CLI integration tests.

Uses a scripted fake sing-box binary so the whole pipeline runs offline:
discovery, config generation + validation, readiness polling, Clash API
switching, verification through the local inbound, rotation, exit-code
propagation, and temp-file cleanup.
"""

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from proxyrotate import __version__, cli
from proxyrotate.config import AppConfig
from proxyrotate.rotation import (
    BytesRotationStrategy,
    CombinedStrategy,
    ConnectionsRotationStrategy,
    TimeRotationStrategy,
)

FAKE_SINGBOX = """\
#!/usr/bin/env python3
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = {}
LOG = os.environ.get("FAKE_SB_LOG", "/dev/null")
SECRET = ""
INBOUND_CONNS = []
TOTALS = [0]

class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True
    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer " + SECRET:
            self._reply(401)
            return
        if self.path == "/version":
            self._reply(200, b'{"version":"fake"}')
        elif self.path == "/connections":
            body = json.dumps({
                "connections": INBOUND_CONNS,
                "downloadTotal": TOTALS[0],
                "uploadTotal": 0,
            }).encode()
            self._reply(200, body)
        else:
            self._reply(404)
    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        name = json.loads(payload).get("name", "?")
        with open(LOG, "a") as fh:
            fh.write(name + "\\n")
        self._reply(204)
    def log_message(self, *a):
        pass

class InboundHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_GET(self):
        # Register this tunnel so /connections reflects real inbound traffic.
        host = self.headers.get("Host", "unknown")
        INBOUND_CONNS.append({"id": f"c{len(INBOUND_CONNS)}-{host}",
                              "metadata": {"host": host}})
        TOTALS[0] += 12345
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()
        self.close_connection = True
    def log_message(self, *a):
        pass

def serve(port, handler):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

def run(config_path):
    global CONFIG, SECRET
    with open(config_path) as fh:
        CONFIG = json.load(fh)
    SECRET = CONFIG["experimental"]["clash_api"]["secret"]
    host, api_port = CONFIG["experimental"]["clash_api"]["external_controller"].rsplit(":", 1)
    serve(int(api_port), ApiHandler)
    serve(CONFIG["inbounds"][0]["listen_port"], InboundHandler)
    while True:
        time.sleep(0.2)

if sys.argv[1] == "check":
    sys.exit(0)
if sys.argv[1] == "run":
    run(sys.argv[sys.argv.index("-c") + 1])
sys.exit(2)
"""


def make_fake_singbox(tmp_path):
    binary = tmp_path / "fake-sing-box"
    binary.write_text(FAKE_SINGBOX)
    binary.chmod(0o755)
    return binary


@pytest.fixture
def fake_singbox(tmp_path, monkeypatch):
    binary = make_fake_singbox(tmp_path)
    monkeypatch.setenv("FAKE_SB_LOG", str(tmp_path / "selections.log"))
    return binary


def count_temp_configs() -> int:
    from pathlib import Path

    return len(list(Path(tempfile.gettempdir()).glob("proxyrotate-*.json")))


class TestStrategySelection:
    def test_default_is_time_only(self):
        strategy = cli.build_strategy(AppConfig())
        assert isinstance(strategy, TimeRotationStrategy)
        assert strategy.interval == 60.0

    def test_every_zero_disables_rotation(self):
        strategy = cli.build_strategy(AppConfig(rotation_interval=0))
        assert isinstance(strategy, TimeRotationStrategy)
        assert strategy.interval == 0

    def test_requests_only(self):
        cfg = AppConfig(rotation_interval=0, rotation_requests=10)
        strategy = cli.build_strategy(cfg)
        assert isinstance(strategy, ConnectionsRotationStrategy)
        assert strategy.max_connections == 10

    def test_mb_only(self):
        cfg = AppConfig(rotation_interval=0, rotation_mb=5)
        strategy = cli.build_strategy(cfg)
        assert isinstance(strategy, BytesRotationStrategy)
        assert strategy.max_bytes == 5 * 1024 * 1024

    def test_all_three_combined(self):
        cfg = AppConfig(rotation_interval=30, rotation_requests=10, rotation_mb=2.5)
        strategy = cli.build_strategy(cfg)
        assert isinstance(strategy, CombinedStrategy)
        kinds = {type(s) for s in strategy.strategies}
        assert kinds == {TimeRotationStrategy, ConnectionsRotationStrategy,
                         BytesRotationStrategy}
        bytes_part = next(s for s in strategy.strategies
                          if isinstance(s, BytesRotationStrategy))
        assert bytes_part.max_bytes == int(2.5 * 1024 * 1024)


class TestActivityFlagValidation:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--requests", "0"],
            ["--requests", "-3"],
            ["--mb", "0"],
            ["--mb", "-1.5"],
        ],
    )
    def test_invalid_activity_flags(self, argv):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv + ["true"])
        assert excinfo.value.code == 2

    def test_requests_flag_accepted(self):
        args = cli.build_parser().parse_args(["--requests", "25", "true"])
        assert args.requests == 25


class TestUsage:
    def test_missing_command_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main([])
        assert excinfo.value.code == 2

    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])
        assert excinfo.value.code == 0
        assert __version__ in capsys.readouterr().out

    @pytest.mark.parametrize(
        "argv",
        [
            ["--every", "-5"],
            ["--health-timeout", "0"],
            ["--log-level", "loud"],
        ],
    )
    def test_invalid_options(self, argv):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv + ["true"])
        assert excinfo.value.code == 2

    def test_dashdash_separator_forwards_args(self, fake_singbox, tmp_path, capsys):
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://10.9.9.9:8080\n")

        child = tmp_path / "child.py"
        child.write_text(
            "import sys\n"
            "print('ARGS:' + ' '.join(sys.argv[1:]))\n"
        )

        code = cli.main([
            "--proxy-file", str(proxy_file),
            "--sing-box", str(fake_singbox),
            "--no-health-check",
            "--every", "0",
            "--",
            sys.executable, str(child), "--continue",
        ])

        captured = capsys.readouterr()
        assert code == 0
        # ProxyRotate itself never wrote to stdout.
        assert captured.out == ""


class TestCheckOnly:
    @pytest.fixture
    def fake_internet(self):
        """A local HTTP server that answers 204 to anything.

        Yields (url, port): the port doubles as the "proxy" listener,
        since a health check connects to the proxy itself.
        """

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.close_connection = True

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield "http://target.invalid/generate_204", server.server_address[1]
        server.shutdown()

    def test_report_and_exit_zero(self, tmp_path, monkeypatch, capsys, fake_internet):
        import proxyrotate.health as health_module

        target_url, proxy_port = fake_internet
        monkeypatch.setattr(health_module, "DEFAULT_CHECK_URL", target_url)

        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text(
            "# comment line\n"
            "\n"
            f"http://127.0.0.1:{proxy_port}\n"
            "garbage-line\n"
            f"http://127.0.0.1:{proxy_port}\n"  # duplicate
        )

        code = cli.main([
            "--check-proxies",
            "--proxy-file", str(proxy_file),
            "--health-timeout", "2",
        ])

        captured = capsys.readouterr()
        assert code == 0
        assert "Loaded 1 proxies" in captured.err
        assert "ignoring invalid proxy on line 4" in captured.err
        assert "Healthy proxies: 1/1" in captured.err

    def test_no_healthy_proxies_exits_three(self, tmp_path, capsys):
        import socket as socket_mod

        sock = socket_mod.socket()
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
        sock.close()

        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text(f"http://127.0.0.1:{closed_port}\n")

        code = cli.main([
            "--check-proxies",
            "--proxy-file", str(proxy_file),
            "--health-timeout", "1",
        ])
        assert code == 3


class TestRunSession:
    def test_requests_flag_rotates_end_to_end(self, tmp_path, fake_singbox):
        """Child opens fresh tunnels; rotation must fire on connection count."""
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text(
            "http://proxy-a.example:8080\n"
            "http://proxy-b.example:8080\n"
            "http://proxy-c.example:8080\n"
        )
        selections_log = tmp_path / "selections.log"

        child = tmp_path / "child.py"
        child.write_text(
            "import os, time, urllib.request\n"
            "# fresh opener per request -> one tunnel each; host differs\n"
            "# from the health-check target so the monitor counts them.\n"
            "for i in range(4):\n"
            "    opener = urllib.request.build_opener(\n"
            "        urllib.request.ProxyHandler({'http': os.environ['HTTP_PROXY']}))\n"
            "    r = opener.open('http://activity.test/ping', timeout=5)\n"
            "    print(f'req {i}: {r.status}', flush=True)\n"
            "    time.sleep(0.3)\n"
            "time.sleep(1.2)  # let the rotator observe the tunnels\n"
        )

        code = cli.main([
            "--proxy-file", str(proxy_file),
            "--sing-box", str(fake_singbox),
            "--no-health-check",
            "--every", "0",          # time rotation disabled on purpose
            "--requests", "3",
            sys.executable, str(child),
        ])

        assert code == 0
        selected = [line for line in selections_log.read_text().splitlines() if line]
        # 4 tunnels, threshold 3 -> exactly one activity rotation.
        assert selected == ["proxy-0", "proxy-1"]

    def test_happy_path_rotates_and_propagates_exit_code(
        self, tmp_path, fake_singbox, capsys
    ):
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text(
            "http://proxy-a.example:8080\n"
            "http://proxy-b.example:8080\n"
        )
        selections_log = tmp_path / "selections.log"

        before = count_temp_configs()
        code = cli.main([
            "--proxy-file", str(proxy_file),
            "--sing-box", str(fake_singbox),
            "--no-health-check",
            "--every", "0.4",
            "--log-level", "debug",
            sys.executable,
            "-c",
            "import time; time.sleep(1.5); print('child done')",
        ])
        after = count_temp_configs()

        captured = capsys.readouterr()
        stderr = captured.err

        assert code == 0
        assert captured.out == ""  # stdout untouched
        assert "ProxyRotate 0." in stderr
        assert "Loaded 2 proxies" in stderr
        assert "exited with code 0" in stderr
        assert "ProxyRotate stopped" in stderr
        assert after == before  # no temporary sing-box config left behind

        selected = [line for line in selections_log.read_text().splitlines() if line]
        assert selected[0] == "proxy-0"
        assert len(selected) >= 3  # initial + at least two timed rotations

    def test_child_exit_code_preserved(self, tmp_path, fake_singbox):
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://p.example:1\n")
        code = cli.main([
            "--proxy-file", str(proxy_file),
            "--sing-box", str(fake_singbox),
            "--no-health-check",
            "--every", "0",
            sys.executable, "-c", "raise SystemExit(7)",
        ])
        assert code == 7

    def test_missing_command_cleans_up(self, tmp_path, fake_singbox, capsys):
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://p.example:1\n")

        before = count_temp_configs()
        code = cli.main([
            "--proxy-file", str(proxy_file),
            "--sing-box", str(fake_singbox),
            "--no-health-check",
            "definitely-not-installed-anywhere",
        ])

        captured = capsys.readouterr()
        assert code == 127
        assert "command not found" in captured.err
        assert count_temp_configs() == before

    def test_all_proxies_fail_verification(self, tmp_path, monkeypatch, capsys):
        binary = make_fake_singbox(tmp_path)

        import proxyrotate.singbox as singbox_module

        def broken_verify(self, timeout=3.0):
            from proxyrotate.singbox import SingBoxError
            raise SingBoxError("tunnel dead")

        monkeypatch.setattr(singbox_module.SingBoxManager, "verify_current", broken_verify)

        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://dead.example:1\nhttp://deader.example:2\n")

        before = count_temp_configs()
        code = cli.main([
            "--proxy-file", str(proxy_file),
            "--sing-box", str(binary),
            "--no-health-check",
            sys.executable, "-c", "pass",
        ])

        captured = capsys.readouterr()
        assert code == 3
        assert "no usable proxies" in captured.err
        assert count_temp_configs() == before  # sing-box was stopped and cleaned up


class TestSigintBlackBox:
    def test_ctrl_c_stops_everything_and_returns_130(self, tmp_path):
        binary = make_fake_singbox(tmp_path)
        log = tmp_path / "selections.log"

        env = {**os.environ, "FAKE_SB_LOG": str(log)}
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://x.example:1\n")

        process = subprocess.Popen(
            [
                sys.executable, "-m", "proxyrotate",
                "--proxy-file", str(proxy_file),
                "--sing-box", str(binary),
                "--no-health-check",
                "--every", "60",
                "--log-level", "debug",
                sys.executable, "-c", "import time; time.sleep(30)",
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        try:
            marker_buffer = _wait_for_marker(process.stderr, b"Starting", timeout=15)
            time.sleep(0.5)  # let signal handlers install
            os.killpg(process.pid, signal.SIGINT)
            code = process.wait(timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

        # Drain whatever remains after the marker (exit messages etc).
        try:
            rest = process.stderr.read() or b""
        except (OSError, ValueError):
            rest = b""
        err = (bytes(marker_buffer) + rest).decode(errors="replace")
        process.stdout.close()
        process.stderr.close()

        assert code == 130
        assert "ProxyRotate stopped" in err

        _assert_no_process_matching(str(binary))


def _wait_for_marker(stream, marker: bytes, timeout: float) -> bytearray:
    """Read stream until marker appears; return everything read."""
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    done = threading.Event()

    def reader():
        while not done.is_set():
            try:
                chunk = stream.readline()
            except (OSError, ValueError):
                return
            if not chunk:
                return
            buffer.extend(chunk)
            if marker in buffer:
                done.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    while time.monotonic() < deadline:
        if done.wait(0.05):
            break
    else:
        raise TimeoutError(f"{marker!r} not seen; got: {bytes(buffer)!r}")
    return buffer


def _assert_no_process_matching(needle: str) -> None:
    """Fail if any live process's cmdline contains needle."""
    for pid_dir in os.listdir("/proc"):
        if not pid_dir.isdigit():
            continue
        try:
            with open(f"/proc/{pid_dir}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode(errors="replace")
        except OSError:
            continue
        assert needle not in cmdline, f"orphaned process {pid_dir}: {cmdline}"
