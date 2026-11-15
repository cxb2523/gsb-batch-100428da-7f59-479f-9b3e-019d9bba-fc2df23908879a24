"""Unit tests for process routing: cgroups, nft batches, sweep, teardown.

Everything runs offline: cgroup roots live in temporary directories and
``routing.nft_run`` is replaced by a recorder that never invokes sudo.
No test touches the real cgroup tree or the host firewall.
"""

from __future__ import annotations

import json
import os
import signal
import socket
from types import SimpleNamespace

import pytest

from proxyrotate import routing
from proxyrotate.routing import (
    RoutingError,
    SessionCgroup,
    chain_names,
)

LIST_TABLE = ("-j", "list", "table", "inet", "proxyrotate")


def _rc(returncode, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class NftRecorder:
    """Stand-in for ``routing.nft_run`` recording every invocation.

    ``list table`` answers per ``table_exists`` (with ``report`` as the
    JSON body); batch calls either succeed or fail per ``fail``.
    """

    def __init__(self, *, table_exists=False, report=None, fail=False):
        self.table_exists = table_exists
        self.report = report if report is not None else {"table": {"chains": []}}
        self.fail = fail
        self.calls = []
        self.batches = []

    def __call__(self, args, *, batch=None, timeout=300):
        self.calls.append((tuple(args), batch))
        if args and args[0] == "-j":
            if not self.table_exists:
                return _rc(
                    4,
                    stderr="Error: Could not process rule: No such file or directory\n",
                )
            return _rc(0, stdout=json.dumps(self.report) + "\n")
        if self.fail:
            return _rc(1, stderr="sudo: a password is required")
        self.batches.append(batch)
        self.table_exists = True
        return _rc(0)

    @property
    def batch_count(self) -> int:
        return len(self.batches)

    def last_batch(self) -> str:
        return self.batches[-1]


@pytest.fixture
def pair(tmp_path, monkeypatch):
    """A ProcessRouting bound to a fake cgroup root + recorded nft."""
    root = tmp_path / "cgroot"
    (root / "user.slice").mkdir(parents=True)
    recorder = NftRecorder()
    monkeypatch.setattr(routing, "nft_run", recorder)
    session = routing.ProcessRouting(
        name="opencode",
        mode="exact",
        resolver="8.8.8.8:53",
        redirect_port=_free_port(),
        dns_port=_free_port(),
        cgroup_root=root,
        parent_relpath="user.slice",
    )
    return session, recorder


class TestSessionCgroup:
    def test_create_makes_directory_and_paths(self, tmp_path):
        parent = tmp_path / "user.slice"
        parent.mkdir()  # in reality: the user's own live cgroup slice
        cgroup = SessionCgroup(
            "ab12cd34", root=tmp_path, parent_relpath="user.slice"
        )
        cgroup.create()
        assert cgroup.path == tmp_path / "user.slice" / "proxyrotate-ab12cd34"
        assert cgroup.path.is_dir()
        assert cgroup.exists()
        assert cgroup.level == 2
        assert cgroup.rel_path == "user.slice/proxyrotate-ab12cd34"
        assert cgroup.procs_path == cgroup.path / "cgroup.procs"

    def test_root_level_session(self, tmp_path):
        cgroup = SessionCgroup("ab12cd34", root=tmp_path, parent_relpath="")
        assert cgroup.level == 1
        assert cgroup.rel_path == "proxyrotate-ab12cd34"
        assert cgroup.path == tmp_path / "proxyrotate-ab12cd34"

    def test_create_failure_is_actionable(self, tmp_path):
        locked = tmp_path / "locked.slice"
        locked.mkdir()
        old = locked.stat().st_mode
        os.chmod(locked, 0o555)
        try:
            cgroup = SessionCgroup(
                "ab12cd34", root=tmp_path, parent_relpath="locked.slice"
            )
            with pytest.raises(RoutingError) as excinfo:
                cgroup.create()
            assert "delegated cgroup" in str(excinfo.value)
        finally:
            os.chmod(locked, old)

    def test_pids_parse_and_missing_file(self, tmp_path):
        cgroup = SessionCgroup("ab12cd34", root=tmp_path, parent_relpath="")
        assert cgroup.pids() == []
        cgroup.path.mkdir()
        cgroup.procs_path.write_text(" 12\n34 \n", encoding="ascii")
        assert cgroup.pids() == [12, 34]
        assert not cgroup.is_empty()

    def test_kill_lingering_terms_then_kills(self, tmp_path, monkeypatch):
        cgroup = SessionCgroup("ab12cd34", root=tmp_path, parent_relpath="")
        cgroup.path.mkdir()
        cgroup.procs_path.write_text("4242\n4243\n", encoding="ascii")

        sent = []

        def fake_kill(pid, sig):
            sent.append((pid, sig))
            raise ProcessLookupError  # simulate "already gone"

        monkeypatch.setattr(routing.os, "kill", fake_kill)
        # A writable tmp filesystem always takes the ``cgroup.kill``
        # path; chmod the group read-only to force the direct-signal
        # fallback (i.e. pre-5.14 kernel behavior).
        old_mode = cgroup.path.stat().st_mode
        os.chmod(cgroup.path, 0o555)
        try:
            cgroup.kill_lingering(grace=0.05, poll=0.01)
        finally:
            os.chmod(cgroup.path, old_mode)

        assert sent[0] == (4242, signal.SIGTERM)
        assert sent[1] == (4243, signal.SIGTERM)
        assert any(sig == signal.SIGKILL for _pid, sig in sent)

    def test_kill_lingering_prefers_kill_file(self, tmp_path, monkeypatch):
        cgroup = SessionCgroup("ab12cd34", root=tmp_path, parent_relpath="")
        cgroup.path.mkdir()
        cgroup.procs_path.write_text("4242\n", encoding="ascii")
        (cgroup.path / "cgroup.kill").write_text("0", encoding="ascii")

        killed = []

        def fake_kill(pid, sig):
            killed.append(pid)

        monkeypatch.setattr(routing.os, "kill", fake_kill)
        cgroup.kill_lingering(grace=0.05, poll=0.01)

        assert (cgroup.path / "cgroup.kill").read_text() == "1"
        assert killed == []

    def test_remove_empty_and_occupied(self, tmp_path):
        cgroup = SessionCgroup("ab12cd34", root=tmp_path, parent_relpath="")
        cgroup.create()
        assert cgroup.remove()
        assert not cgroup.exists()
        cgroup.create()
        cgroup.procs_path.write_text("99\n", encoding="ascii")
        assert cgroup.remove() is False
        assert cgroup.exists()

    def test_own_cgroup_relpath(self, tmp_path):
        file = tmp_path / "cgroup"
        file.write_text("0::/user.slice/u.service\n", encoding="ascii")
        assert routing.own_cgroup_relpath(file) == "user.slice/u.service"

        file.write_text("12:devices:/x\n", encoding="ascii")
        with pytest.raises(RoutingError):
            routing.own_cgroup_relpath(file)


class TestFindInstalledCgroup:
    def test_finds_and_restores_relpath(self, tmp_path):
        nested = tmp_path / "user.slice" / "u.service"
        nested.mkdir(parents=True)
        (nested / "proxyrotate-ab12cd34").mkdir()
        found = routing.find_installed_cgroup(tmp_path, "ab12cd34")
        assert found is not None
        assert found.rel_path == "user.slice/u.service/proxyrotate-ab12cd34"
        assert found.level == 3

    def test_missing(self, tmp_path):
        assert routing.find_installed_cgroup(tmp_path, "ab12cd34") is None


class TestBatchBuilders:
    def test_install_batch_structure(self, pair):
        session, _ = pair
        rid = session.rid
        batch = session.build_install_batch(table_exists=False)

        assert "add table inet proxyrotate" in batch
        assert (
            f"add chain inet proxyrotate PR-{rid}-nat "
            "{ type nat hook output priority dstnat; policy accept; }" in batch
        )
        assert (
            f"add chain inet proxyrotate PR-{rid}-fil "
            "{ type filter hook output priority 0; policy accept; }" in batch
        )

        member = f'socket cgroupv2 level 2 "user.slice/proxyrotate-{rid}"'
        assert member in batch
        # NAT rules: loopback pass-throughs first (DNS excluded), then the
        # redirects with the session's ports.
        assert f"{member} ip daddr 127.0.0.0/8 tcp dport != 53 accept" in batch
        assert f"{member} ip daddr 127.0.0.0/8 udp dport != 53 accept" in batch
        assert f"{member} ip6 daddr ::1 tcp dport != 53 accept" in batch
        assert f"{member} ip6 daddr ::1 udp dport != 53 accept" in batch
        assert (
            f"{member} meta l4proto tcp redirect to :{session.redirect_port}"
            in batch
        )
        assert (
            f"{member} meta l4proto udp udp dport 53 redirect to "
            f":{session.dns_port}" in batch
        )
        # Filter chain: narrow exceptions, then the fail-closed drop.
        assert f"{member} ip daddr 224.0.0.0/4 accept" in batch
        assert "nd-neighbor-solicit" in batch
        assert f"{member} counter drop" in batch
        # No un-substituted placeholders remain.
        assert "PORT_" not in batch

    def test_install_batch_without_table_creation(self, pair):
        session, _ = pair
        batch = session.build_install_batch(table_exists=True)
        assert "add table" not in batch
        assert f"add chain inet proxyrotate PR-{session.rid}-nat" in batch

    def test_remove_batch_targets_only_own_chains(self, pair):
        session, _ = pair
        rid = session.rid
        batch = session.build_remove_batch()
        assert batch == (
            f"flush chain inet proxyrotate PR-{rid}-nat\n"
            f"delete chain inet proxyrotate PR-{rid}-nat\n"
            f"flush chain inet proxyrotate PR-{rid}-fil\n"
            f"delete chain inet proxyrotate PR-{rid}-fil\n"
        )

    def test_chain_names(self):
        assert chain_names("ab") == ("PR-ab-nat", "PR-ab-fil")


class TestInstall:
    def test_installs_when_table_missing(self, pair):
        session, recorder = pair
        session.install()

        assert recorder.calls[0][0] == LIST_TABLE
        assert recorder.calls[1][0] == ("-f", "-")
        assert recorder.last_batch() == session.build_install_batch(
            table_exists=False
        )
        assert recorder.batch_count == 1

    def test_install_failure_propagates(self, tmp_path, monkeypatch):
        root = tmp_path / "cgroot"
        (root / "user.slice").mkdir(parents=True)
        recorder = NftRecorder(fail=True)
        monkeypatch.setattr(routing, "nft_run", recorder)
        session = routing.ProcessRouting(
            name="opencode", resolver="1.1.1.1",
            redirect_port=_free_port(), dns_port=_free_port(),
            cgroup_root=root, parent_relpath="user.slice",
        )
        with pytest.raises(RoutingError) as excinfo:
            session.install()
        assert "sudo" in str(excinfo.value)
        # The list success came from the ENOENT path; the batch never
        # ran, so nothing was written.
        assert recorder.batches == []

    def test_probe_password_error_is_not_treated_as_missing_table(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "cgroot"
        (root / "user.slice").mkdir(parents=True)
        session = routing.ProcessRouting(
            name="opencode", resolver="1.1.1.1",
            redirect_port=_free_port(), dns_port=_free_port(),
            cgroup_root=root, parent_relpath="user.slice",
        )

        def password_error(args, *, batch=None, timeout=300):
            return _rc(1, stderr="sudo: a password is required")

        monkeypatch.setattr(routing, "nft_run", password_error)
        with pytest.raises(RoutingError) as excinfo:
            session.install()
        assert "password" in str(excinfo.value)


class TestTeardown:
    def test_full_teardown_removes_rules_and_cgroup(self, pair):
        session, recorder = pair
        session.prepare(sweep_stale=False)
        session.install()
        session.teardown()

        assert session.cgroup.exists() is False
        assert recorder.batch_count == 2
        assert recorder.last_batch() == session.build_remove_batch()

        # idempotent: a second teardown adds nothing
        session.teardown()
        assert recorder.batch_count == 2

    def test_teardown_keeps_rules_while_group_occupied(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "cgroot"
        (root / "user.slice").mkdir(parents=True)
        recorder = NftRecorder()
        monkeypatch.setattr(routing, "nft_run", recorder)
        monkeypatch.setattr(routing.os, "kill", lambda pid, sig: None)
        session = routing.ProcessRouting(
            name="opencode", resolver="1.1.1.1",
            redirect_port=_free_port(), dns_port=_free_port(),
            cgroup_root=root, parent_relpath="user.slice",
        )
        session.prepare(sweep_stale=False)
        session.install()
        # A stubborn member the fake filesystem keeps listing:
        session.cgroup.procs_path.write_text("999\n", encoding="ascii")

        session.teardown()

        # No remove batch: rules stay (fail-closed) while the occupied
        # group cannot be emptied.
        assert recorder.batch_count == 1
        assert session.cgroup.exists()

    def test_teardown_without_install_leaves_no_batch(self, pair):
        session, recorder = pair
        session.prepare(sweep_stale=False)
        session.teardown()
        assert recorder.batch_count == 0
        assert session.cgroup.exists() is False


class TestSweep:
    def _report(self, *rids):
        chains = []
        for rid in rids:
            chains.append({"name": f"PR-{rid}-nat"})
            chains.append({"name": f"PR-{rid}-fil"})
        return json.dumps({"table": {"chains": chains}})

    def test_sweeps_empty_and_missing_sessions(self, pair):
        session, recorder = pair
        # livec0ffee is a live session (occupied cgroup); deadbeef has
        # no directory at all.
        live_dir = (
            session.cgroup.root / "user.slice" / "proxyrotate-livec0ffee"
        )
        live_dir.mkdir()
        (live_dir / "cgroup.procs").write_text("999\n", encoding="ascii")

        swept = session.sweep(listing=self._report("livec0ffee", "deadbeef"))

        assert swept == ["deadbeef"]
        batch = recorder.last_batch()
        assert "delete chain inet proxyrotate PR-deadbeef-nat" in batch
        assert "delete chain inet proxyrotate PR-deadbeef-fil" in batch
        assert "PR-livec0ffee" not in batch
        assert "delete table" not in batch
        assert live_dir.exists()

    def test_deletes_table_when_nothing_remains(self, pair):
        session, recorder = pair
        swept = session.sweep(listing=self._report("ab12cd34", "ef123456"))
        assert sorted(swept) == ["ab12cd34", "ef123456"]
        assert recorder.last_batch() == "delete table inet proxyrotate\n"

        # the swept cgroup directories were absent, nothing to remove
        assert routing.find_installed_cgroup(
            session.cgroup.root, "ab12cd34"
        ) is None

    def test_keeps_table_with_foreign_chains(self, pair):
        session, recorder = pair
        report = json.dumps(
            {
                "table": {
                    "chains": [
                        {"name": "PR-deadbeef-nat"},
                        {"name": "PR-deadbeef-fil"},
                        {"name": "docker-user"},
                    ]
                }
            }
        )
        swept = session.sweep(listing=report)
        assert swept == ["deadbeef"]
        assert any(
            "delete chain inet proxyrotate PR-deadbeef" in b
            for b in recorder.batches
        )
        assert not any("delete table" in b for b in recorder.batches)

    def test_live_session_not_swept(self, pair):
        session, recorder = pair
        live_dir = (
            session.cgroup.root / "user.slice" / "proxyrotate-ab12cd34"
        )
        live_dir.mkdir()
        (live_dir / "cgroup.procs").write_text("999\n", encoding="ascii")

        swept = session.sweep(listing=self._report("ab12cd34"))
        assert swept == []
        assert recorder.batch_count == 0
        assert live_dir.exists()


class TestPrepare:
    def test_prepare_creates_cgroup_without_sweep_when_table_missing(self, pair):
        session, recorder = pair
        session.prepare(sweep_stale=True)
        assert session.cgroup.exists()
        # Only the missing-table probe ran; no sweep batches:
        assert [call[0][0] for call in recorder.calls] == ["-j"]

    def test_prepare_sweeps_when_table_present(self, pair, monkeypatch):
        session, recorder = pair

        dead_dir = session.cgroup.root / "user.slice" / "proxyrotate-deadbeef"
        dead_dir.mkdir()
        recorder.table_exists = True
        recorder.report = json.loads(
            json.dumps(
                {
                    "table": {
                        "chains": [
                            {"name": "PR-deadbeef-nat"},
                            {"name": "PR-deadbeef-fil"},
                        ]
                    }
                }
            )
        )

        session.prepare(sweep_stale=True)

        assert any(
            "delete chain inet proxyrotate PR-deadbeef" in b
            for b in recorder.batches
        )
        assert recorder.last_batch() == "delete table inet proxyrotate\n"
        assert session.cgroup.exists()  # this session's group was created
        assert not dead_dir.exists()    # abandoned group swept
