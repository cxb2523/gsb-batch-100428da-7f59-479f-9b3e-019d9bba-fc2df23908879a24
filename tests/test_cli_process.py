"""CLI tests for --process-name routing.

The routing object itself is a stub installed over ``cli.create_routing``
(the documented test seam), so no test here touches sudo, nftables, or
the real cgroup tree; sing-box is the scripted fake from test_cli.
The one real bit exercised is the pre-exec cgroup join: the child
actually writes its PID into the stub's ``cgroup.procs`` file.
"""

from __future__ import annotations

import json
import shutil
import sys

import pytest

from proxyrotate import cli
from proxyrotate.routing import RoutingError
from proxyrotate.singbox import SingBoxError, _free_port


class StubCgroup:
    """A fake session cgroup directory inside tmp."""

    def __init__(self, path):
        self.path = path
        self.procs_path = path / "cgroup.procs"

    def create(self):
        self.path.mkdir(parents=True)
        self.procs_path.touch()


class StubRouting:
    """Records the lifecycle calls run_session must make, in order.

    Mirrors the real object's failure surface: ``prepare_error`` /
    ``install_error`` / ``relay_error`` raise RoutingError from the
    respective lifecycle step, for the startup-failure tests.
    """

    def __init__(self, tmp_path, *, name, mode):
        self.program_name = name
        self.mode = mode
        self.redirect_port = _free_port()
        self.dns_port = _free_port()
        self.cgroup = StubCgroup(
            tmp_path / "user.slice" / "proxyrotate-stubname"
        )
        self.events: list[object] = []
        self.joined_pids: list[int] = []
        self.prepare_error: RoutingError | None = None
        self.install_error: RoutingError | None = None
        self.relay_error: RoutingError | None = None

    def prepare(self):
        if self.prepare_error is not None:
            raise self.prepare_error
        self.events.append("prepare")
        self.cgroup.create()

    def start_dns_relay(self, *, inbound_port: int):
        if self.relay_error is not None:
            raise self.relay_error
        self.events.append(("relay", inbound_port))

    def install(self):
        if self.install_error is not None:
            raise self.install_error
        self.events.append("install")

    def teardown(self):
        self.events.append("teardown")
        try:  # snapshot who joined before nuking the fake directory
            self.joined_pids = [
                int(tok) for tok in self.cgroup.procs_path.read_text().split()
            ]
        except OSError:
            pass
        shutil.rmtree(self.cgroup.path, ignore_errors=True)


@pytest.fixture
def route(tmp_path, monkeypatch):
    """Factory: build a StubRouting bound to the ``create_routing`` seam.

    ``factory.realized`` holds only stubs actually handed out through
    ``cli.create_routing`` — a validation abort never builds one, while
    the factory itself is called by the test to install the stub.
    """
    realized = []

    def factory(*, name="opencode", mode="exact"):
        stub = StubRouting(tmp_path, name=name, mode=mode)
        monkeypatch.setattr(
            cli, "create_routing",
            lambda *a, **k: (realized.append(stub), stub)[1],
        )
        return stub

    factory.realized = realized
    return factory


def write_proxy_file(tmp_path) -> str:
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("http://p.example:1\n")
    return str(proxy_file)


def stub_argv(tmp_path, fake_singbox, extra: list[str]) -> list[str]:
    return [
        "--proxy-file", write_proxy_file(tmp_path),
        "--sing-box", str(fake_singbox),
        "--no-health-check",
        "--every", "0",
        *extra,
    ]


class TestFlagParsing:
    def test_flags_map_into_config(self):
        args = cli.build_parser().parse_args([
            "--process-name", "opencode",
            "--process-name-mode", "substring",
            "--process-dns", "8.8.8.8:1053",
            "true",
        ])
        config = cli.merge_configuration(args)
        assert config.process_name == "opencode"
        assert config.process_name_mode == "substring"
        assert config.process_dns == "8.8.8.8:1053"

    def test_defaults_when_only_name_given(self):
        args = cli.build_parser().parse_args([
            "--process-name", "opencode", "true",
        ])
        config = cli.merge_configuration(args)
        assert config.process_name_mode == "exact"     # config default
        assert config.process_dns == "1.1.1.1"         # config default

    def test_invalid_mode_is_a_usage_error(self):
        with pytest.raises(SystemExit) as excinfo:
            cli.build_parser().parse_args([
                "--process-name", "x", "--process-name-mode", "fuzzy", "true",
            ])
        assert excinfo.value.code == 2

    def test_config_file_keys_are_read(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            'process_name = "from-file"\n'
            'process_name_mode = "substring"\n'
            'process_dns = "9.9.9.9:5353"\n'
        )
        args = cli.build_parser().parse_args([
            "--config", str(config_file),
            # No --process-name flag at all: the file must activate routing.
            "true",
        ])
        config = cli.merge_configuration(args)
        assert config.process_name == "from-file"
        assert config.process_name_mode == "substring"
        assert config.process_dns == "9.9.9.9:5353"


class TestNamePolicy:
    def test_flag_off_never_touches_routing(
        self, tmp_path, fake_singbox, capsys, monkeypatch
    ):
        """No --process-name flag: no routing object is ever built."""
        def forbidden(*_a, **_k):
            pytest.fail("create_routing must not run without --process-name")

        monkeypatch.setattr(cli, "create_routing", forbidden)
        args = stub_argv(tmp_path, fake_singbox, [
            sys.executable, "-c", "pass",
        ])
        assert cli.main(args) == 0
        assert capsys.readouterr().out == ""

    def test_mismatch_aborts_before_any_setup(self, tmp_path, route, capsys):
        stub = route()  # announced name: opencode
        child = _child_script(tmp_path, tmp_path / "unused.json")
        argv = [
            "--proxy-file", write_proxy_file(tmp_path),
            "--process-name", "different-name",
            "--no-health-check",
            str(child),
        ]
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == 2
        assert "does not match" in capsys.readouterr().err
        assert route.realized == []          # create_routing never reached
        assert stub.events == []             # nothing was set up

    def test_exact_name_matches_launched_program(
        self, tmp_path, fake_singbox, route, capsys
    ):
        route()  # name "opencode"; exact basename match
        child = _child_script(tmp_path, tmp_path / "unused.json")
        args = stub_argv(tmp_path, fake_singbox, [
            "--process-name", "opencode",
            str(child),
        ])
        # Child reports its identity, sleeps, exits 7.
        assert cli.main(args) == 7
        assert capsys.readouterr().out == ""

    def test_substring_mode_accepts_partial(
        self, tmp_path, fake_singbox, route, capsys
    ):
        route(name="open", mode="substring")
        child = _child_script(tmp_path, tmp_path / "unused.json")
        args = stub_argv(tmp_path, fake_singbox, [
            "--process-name", "open",
            "--process-name-mode", "substring",
            str(child),
        ])
        assert cli.main(args) == 7
        assert capsys.readouterr().out == ""

    def test_exact_mode_rejects_partial_name(self, tmp_path, route, capsys):
        route()  # name "opencode"; exact must not match "open"
        child = _child_script(tmp_path, tmp_path / "unused.json")
        argv = [
            "--proxy-file", write_proxy_file(tmp_path),
            "--process-name", "open",
            "--no-health-check",
            str(child),
        ]
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == 2
        assert route.realized == []

    def test_non_linux_platform_is_rejected(
        self, tmp_path, monkeypatch, capsys, route
    ):
        route()  # announced name matches, the platform must fail first
        monkeypatch.setattr(sys, "platform", "darwin")
        child = _child_script(tmp_path, tmp_path / "unused.json")
        argv = [
            "--proxy-file", write_proxy_file(tmp_path),
            "--process-name", "opencode",
            "--no-health-check",
            str(child),
        ]
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == 2
        assert "requires Linux" in capsys.readouterr().err
        assert route.realized == []


def _child_script(tmp_path, marker):
    """A small executable ``opencode`` (basename matches the stub name).

    Reports its	pid and inherited env into ``marker``, then exits 7.
    """
    path = tmp_path / "opencode"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, time\n"
        f"info = {{'pid': os.getpid(),\n"
        f"          'http_proxy': os.environ.get('HTTP_PROXY', '')}}\n"
        f"open({str(marker)!r}, 'w').write(json.dumps(info))\n"
        "time.sleep(0.4)\n"
        "raise SystemExit(7)\n"
    )
    path.chmod(0o755)
    return path


class TestWiring:
    def test_successful_session_order_and_child_joins_group(
        self, tmp_path, fake_singbox, route, capsys
    ):
        stub = route()  # announced name: opencode
        marker = tmp_path / "child-reported.json"
        child = _child_script(tmp_path, marker)

        args = stub_argv(tmp_path, fake_singbox, [
            "--process-name", "opencode",
            str(child),
        ])
        code = cli.main(args)
        assert code == 7
        assert capsys.readouterr().out == ""

        # Lifecycle order: prepare -> relay -> install -> (app) -> teardown.
        assert stub.events[0] == "prepare"
        assert stub.events[1][0] == "relay"
        relay_inbound_port = stub.events[1][1]
        assert stub.events[2] == "install"
        assert stub.events[3] == "teardown"
        assert len(stub.events) == 4

        # The child really joined the (stub) cgroup between fork and exec;
        # teardown snapshotted the procs file before cleaning the dir.
        pids = stub.joined_pids
        reported = json.loads(marker.read_text())
        assert pids == [reported["pid"]]

        # The child inherited the proxy env pointing at sing-box's inbound,
        # which is exactly where the relay was pointed.
        assert reported["http_proxy"].startswith("http://127.0.0.1:")
        assert int(reported["http_proxy"].rsplit(":", 1)[1]) \
            == relay_inbound_port

        # Teardown cleaned the stub cgroup directory like the real one.
        assert not stub.cgroup.path.exists()

    def test_prepare_failure_exits_one_without_app(
        self, tmp_path, fake_singbox, route, capsys
    ):
        stub = route()
        stub.prepare_error = RoutingError("not delegated")
        marker = tmp_path / "child-reported.json"
        child = _child_script(tmp_path, marker)

        args = stub_argv(tmp_path, fake_singbox, [
            "--process-name", "opencode",
            str(child),
        ])
        assert cli.main(args) == 1

        err = capsys.readouterr()
        assert "routing setup failed: not delegated" in err.err
        assert "Starting" not in err.err
        # Cleanup is still attempted (a no-op here) so nothing can leak.
        assert stub.events == ["teardown"]
        assert not marker.exists()               # the app never started

    def test_install_failure_exits_one_without_app(
        self, tmp_path, fake_singbox, route, capsys
    ):
        stub = route()
        stub.install_error = RoutingError("sudo: a password is required")
        marker = tmp_path / "child-reported.json"
        child = _child_script(tmp_path, marker)

        args = stub_argv(tmp_path, fake_singbox, [
            "--process-name", "opencode",
            str(child),
        ])
        assert cli.main(args) == 1

        err = capsys.readouterr()
        assert "routing setup failed:" in err.err
        assert "Starting" not in err.err
        # relay ran, install failed (no "install" event), then cleanup.
        assert stub.events[0] == "prepare"
        assert stub.events[1][0] == "relay"
        assert stub.events[2] == "teardown"
        assert len(stub.events) == 3
        assert not marker.exists()
        assert not stub.cgroup.path.exists()     # finally-block teardown ran

    def test_singbox_failure_after_prepare_cleans_cgroup(
        self, tmp_path, fake_singbox, route, capsys, monkeypatch
    ):
        stub = route()
        marker = tmp_path / "child-reported.json"
        child = _child_script(tmp_path, marker)

        def broken_start(self, timeout=3.0):
            raise SingBoxError("sing-box died")

        monkeypatch.setattr(cli.SingBoxManager, "start", broken_start)

        args = stub_argv(tmp_path, fake_singbox, [
            "--process-name", "opencode",
            str(child),
        ])
        assert cli.main(args) == 4

        err = capsys.readouterr()
        assert "sing-box died" in err.err
        assert "Starting" not in err.err
        # prepare succeeded, then sing-box failed -> the cgroup must be
        # released before returning (regression test for the leak).
        assert stub.events == ["prepare", "teardown"]
        assert not marker.exists()
        assert not stub.cgroup.path.exists()
