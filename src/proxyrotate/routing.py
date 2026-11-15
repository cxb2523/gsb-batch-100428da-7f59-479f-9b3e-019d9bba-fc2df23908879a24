"""OS-level routing of the launched command tree (Linux).

A unique cgroup v2 directory is created inside the user's delegated
cgroup slice and the launched command joins it between ``fork()`` and
``exec()`` (see :mod:`proxyrotate.process`), so the command and every
descendant it ever spawns is a member from creation on — no /proc
scans, no name races. nftables rules scoped to that cgroup via the
``socket cgroupv2`` expression redirect the group's TCP into a local
sing-box ``redirect`` inbound and its DNS into an in-process relay
(:mod:`proxyrotate.dnsrelay`); everything else from the group that the
proxy infrastructure cannot carry is dropped, so failures stay
fail-closed. sing-box itself runs outside the group and is never
matched, re-routed, or affected.

Naming and cleanup: every artifact carries a random session id ``<rid>``
— cgroup ``proxyrotate-<rid>``, nft chains ``PR-<rid>-nat`` /
``PR-<rid>-fil`` inside table ``inet proxyrotate``. The table and the
user's ruleset are never flushed; only this session's chains and
directory are removed. A sweep at startup removes the artifacts of
abandoned sessions whose cgroup is gone or empty; a session whose group
still holds processes keeps its rules (fail-closed) until it drains.

Privileges: cgroup creation is unprivileged (systemd delegates the
user's subtree); installing and removing rules needs root and runs as
narrow ``sudo nft`` batches only. ProxyRotate itself never gains root.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import signal
import subprocess
import time
from pathlib import Path

from .logging import get_logger

NFT_FAMILY = "inet"
NFT_TABLE = "proxyrotate"
CGROUP_PREFIX = "proxyrotate-"
CHAIN_PREFIX = "PR-"
DEFAULT_RESOLVER = "1.1.1.1"

_ROOT = Path("/sys/fs/cgroup")
_PROC_CGROUP = Path("/proc/self/cgroup")

_PROCS_NAME = "cgroup.procs"
_KILL_NAME = "cgroup.kill"

_CHAIN_RE = re.compile(
    rf"^{re.escape(CHAIN_PREFIX)}([0-9a-f]+)-(nat|fil)$"
)

# nft reports a missing table as ESRCH/ENOENT; sudo's own failures
# (password prompts, no tty) never contain this phrase, so it is safe
# to treat as "table does not exist".
_MISSING_TABLE_TEXT = "no such file"


class RoutingError(RuntimeError):
    """Raised for cgroup, sudo/nft, or ruleset failures."""


def chain_names(rid: str) -> tuple[str, str]:
    """The two per-session chain names for ``rid``."""
    return f"{CHAIN_PREFIX}{rid}-nat", f"{CHAIN_PREFIX}{rid}-fil"


def parse_resolver(text: str) -> tuple[str, int]:
    """Parse a ``HOST[:PORT]`` plain-DNS resolver spec."""
    host, sep, port_part = text.partition(":")
    host = host.strip()
    if not host:
        raise RoutingError("process DNS resolver must not be empty (HOST[:PORT])")
    port = 53
    if sep:
        try:
            port = int(port_part)
        except ValueError as exc:
            raise RoutingError(
                f"process DNS resolver port must be a number: {port_part!r}"
            ) from exc
        if not 1 <= port <= 65535:
            raise RoutingError(
                f"process DNS resolver port must be 1..65535, got {port}"
            )
    return host, port


def own_cgroup_relpath(proc_cgroup_file: Path = _PROC_CGROUP) -> str:
    """Relative cgroup v2 path of this process (e.g. ``user.slice/...``)."""
    try:
        lines = proc_cgroup_file.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise RoutingError(f"cannot read {proc_cgroup_file}: {exc}") from exc
    for line in lines:
        if line.startswith("0::"):
            return line[3:].lstrip("/")
    raise RoutingError("cgroup v2 not in use: no '0::' entry in /proc/self/cgroup")


def nft_run(args: list[str], *, batch: str | None = None,
            timeout: float = 300.0) -> subprocess.CompletedProcess:
    """Run one narrow ``sudo nft`` invocation.

    Only nft ever goes through sudo. Batches are all-or-nothing
    transactions. ``timeout`` covers a password prompt.
    """
    return subprocess.run(
        ["sudo", "nft", *args],
        input=batch,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,  # the returncode is inspected by the callers
    )


def _chain_names_from_report(payload: dict) -> set[str]:
    """Collect chain names from ``nft -j list table`` output."""
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            chains = node.get("chains")
            if isinstance(chains, list):
                for entry in chains:
                    if isinstance(entry, dict) and "name" in entry:
                        names.add(entry["name"])
            else:
                for value in node.values():
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return names


def _extract_chain_names(text: str) -> set[str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict):
        return set()
    return _chain_names_from_report(payload)


class SessionCgroup:
    """A unique cgroup v2 directory for one routing session."""

    def __init__(self, rid: str, *, root: Path = _ROOT,
                 parent_relpath: str | None = None):
        self.rid = rid
        self.root = Path(root)
        self.parent_relpath = (
            parent_relpath if parent_relpath is not None else own_cgroup_relpath()
        )
        self.name = f"{CGROUP_PREFIX}{rid}"

    # -- paths ---------------------------------------------------------------

    @property
    def rel_path(self) -> str:
        """Path relative to the hierarchy root, used for nft matching."""
        return f"{self.parent_relpath}/{self.name}" if self.parent_relpath \
            else self.name

    @property
    def path(self) -> Path:
        return self.root / self.rel_path

    @property
    def procs_path(self) -> Path:
        return self.path / _PROCS_NAME

    @property
    def level(self) -> int:
        """Number of path components — the ``socket cgroupv2`` level."""
        return len(self.rel_path.split("/"))

    # -- lifecycle -----------------------------------------------------------

    def create(self) -> None:
        try:
            self.path.mkdir()
        except OSError as exc:
            raise RoutingError(
                f"could not create cgroup {self.path}: {exc}\n"
                "process routing needs a writable delegated cgroup v2 slice "
                "(a systemd user session provides one)"
            ) from exc

    def exists(self) -> bool:
        return self.path.is_dir()

    def pids(self) -> list[int]:
        try:
            text = self.procs_path.read_text(encoding="ascii")
        except OSError:
            return []
        return [int(tok) for tok in text.split()]

    def is_empty(self) -> bool:
        return not self.pids()

    def kill_lingering(self, grace: float = 2.0, poll: float = 0.1) -> None:
        """Kill the group via ``cgroup.kill``, or TERM→KILL without it."""
        pids = self.pids()
        if not pids:
            return

        kill_file = self.path / _KILL_NAME
        try:
            # Kernel ≥ 5.14: one atomic write kills the whole group
            # synchronously, so no per-pid signals are ever needed.
            kill_file.write_text("1", encoding="ascii")
        except OSError:
            # Pre-5.14 kernel (no knob) or a failed write: signal
            # members directly, TERM first, then KILL stragglers.
            for pid in pids:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGTERM)

            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and self.pids():
                time.sleep(poll)

            for pid in self.pids():
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
            return

        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and self.pids():
            time.sleep(poll)

    def remove(self) -> bool:
        """rmdir the directory; False when it still contains processes."""
        try:
            self.path.rmdir()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


def find_installed_cgroup(root: Path, rid: str) -> SessionCgroup | None:
    """Locate an existing ``proxyrotate-<rid>`` directory under ``root``."""
    target = f"{CGROUP_PREFIX}{rid}"
    for dirpath, dirnames, _ in os.walk(root):
        if target in dirnames:
            rel_parent = (Path(dirpath) / target).relative_to(root).parent
            return SessionCgroup(rid, root=Path(root),
                                 parent_relpath=str(rel_parent))
    return None


class ProcessRouting:
    """Owns one session's cgroup, nftables rules, and DNS relay."""

    def __init__(self, name: str, *, mode: str = "exact",
                 resolver: str = DEFAULT_RESOLVER,
                 redirect_port: int,
                 dns_port: int,
                 cgroup_root: Path = _ROOT,
                 parent_relpath: str | None = None,
                 logger=None):
        self.program_name = name
        self.mode = mode
        self.resolver = parse_resolver(resolver)
        self.redirect_port = redirect_port
        self.dns_port = dns_port
        self.log = logger or get_logger("routing")

        self.rid = secrets.token_hex(4)
        self.nat_chain, self.fil_chain = chain_names(self.rid)
        self.cgroup = SessionCgroup(self.rid, root=cgroup_root,
                                    parent_relpath=parent_relpath)

        self._dns_relay = None
        self._installed = False

    # -- identity ------------------------------------------------------------

    @property
    def chains(self) -> tuple[str, str]:
        return self.nat_chain, self.fil_chain

    def _member_expr(self) -> str:
        return f'socket cgroupv2 level {self.cgroup.level} "{self.cgroup.rel_path}"'

    # -- privileged helpers ----------------------------------------------------

    def _run_nft(self, args: list[str], *, batch: str | None = None,
                 timeout: float = 300.0) -> subprocess.CompletedProcess:
        try:
            result = nft_run(args, batch=batch, timeout=timeout)
        except FileNotFoundError as exc:
            raise RoutingError(
                "sudo or nft not found; install the nftables package "
                "to use process routing"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RoutingError(
                f"sudo nft did not finish within {timeout:.0f}s"
            ) from exc
        except OSError as exc:
            raise RoutingError(f"could not run sudo nft: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            args_text = " ".join(args)
            raise RoutingError(f"sudo nft {args_text!r} failed:\n{detail}")
        return result

    def _table_state(self) -> bool:
        """True/False for table existence; raises on other nft failures."""
        try:
            self._run_nft(["-j", "list", "table", NFT_FAMILY, NFT_TABLE])
        except RoutingError as exc:
            if _MISSING_TABLE_TEXT in str(exc).lower():
                return False
            raise
        return True

    # -- nft batch builders ----------------------------------------------------

    def build_install_batch(self, *, table_exists: bool) -> str:
        """The all-or-nothing install batch for this session."""
        member = self._member_expr()
        redirect = f"redirect to :{self.redirect_port}"
        dns = f"redirect to :{self.dns_port}"

        # Order matters: loopback is allowed first (with DNS ports
        # excluded so name resolution is still consumed by us), then the
        # redirects carry everything else into sing-box.
        nat_rules = [
            f"{member} ip daddr 127.0.0.0/8 tcp dport != 53 accept",
            f"{member} ip daddr 127.0.0.0/8 udp dport != 53 accept",
            f"{member} ip6 daddr ::1 tcp dport != 53 accept",
            f"{member} ip6 daddr ::1 udp dport != 53 accept",
            f"{member} meta l4proto tcp {redirect}",
            f"{member} meta l4proto udp udp dport 53 {dns}",
        ]
        # Fail-closed filter chain: the exceptions are local and minimal;
        # everything the proxy path cannot carry is dropped.
        fil_rules = [
            f"{member} ip daddr 127.0.0.0/8 accept",
            f"{member} ip6 daddr ::1 accept",
            f"{member} ip daddr 224.0.0.0/4 accept",
            f"{member} ip6 daddr {{ fe80::/64, ff00::/8 }} accept",
            (
                f"{member} meta l4proto icmpv6 icmpv6 type {{ "
                "nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, "
                "nd-router-advert, packet-too-big } accept"
            ),
            f"{member} meta l4proto tcp accept",
            f"{member} counter drop",
        ]

        lines: list[str] = []
        if not table_exists:
            lines.append(f"add table {NFT_FAMILY} {NFT_TABLE}")
        lines.append(
            f"add chain {NFT_FAMILY} {NFT_TABLE} {self.nat_chain} "
            "{ type nat hook output priority dstnat; policy accept; }"
        )
        lines.append(
            f"add chain {NFT_FAMILY} {NFT_TABLE} {self.fil_chain} "
            "{ type filter hook output priority 0; policy accept; }"
        )
        lines.extend(
            f"add rule {NFT_FAMILY} {NFT_TABLE} {self.nat_chain} {rule}"
            for rule in nat_rules
        )
        lines.extend(
            f"add rule {NFT_FAMILY} {NFT_TABLE} {self.fil_chain} {rule}"
            for rule in fil_rules
        )
        return "\n".join(lines) + "\n"

    def build_remove_batch(self, rids: list[str] | None = None) -> str:
        """The remove batch: only chains owned by the given session ids."""
        rids = rids if rids is not None else [self.rid]
        lines: list[str] = []
        for rid in rids:
            nat_chain, fil_chain = chain_names(rid)
            for chain in (nat_chain, fil_chain):
                lines.append(f"flush chain {NFT_FAMILY} {NFT_TABLE} {chain}")
                lines.append(f"delete chain {NFT_FAMILY} {NFT_TABLE} {chain}")
        return "\n".join(lines) + "\n"

    # -- lifecycle -------------------------------------------------------------

    def prepare(self, sweep_stale: bool = True) -> None:
        """Sweep previous abandoned sessions, then create this cgroup."""
        if sweep_stale and self._table_state():
            listing = self._run_nft(
                ["-j", "list", "table", NFT_FAMILY, NFT_TABLE]
            ).stdout or ""
            cleaned = self.sweep(listing=listing)
            if cleaned:
                self.log.info(
                    "cleaned abandoned routing session(s): %s", ", ".join(cleaned)
                )
        self.cgroup.create()
        self.log.info(
            "routing cgroup %s (level %d)", self.cgroup.rel_path, self.cgroup.level
        )

    def install(self) -> None:
        """Install this session's rules (one atomic sudo batch)."""
        exists = self._table_state()
        batch = self.build_install_batch(table_exists=exists)
        self.log.info("installing nft rules:\n%s", batch)
        self._run_nft(["-f", "-"], batch=batch)
        self._installed = True
        self.log.info(
            "routing rules active (table %s, chains %s/%s, cgroup %s, "
            "redirect :%d, dns :%d)",
            NFT_TABLE, self.nat_chain, self.fil_chain, self.cgroup.rel_path,
            self.redirect_port, self.dns_port,
        )

    def teardown(self) -> None:
        """Stop the relay, stop stragglers, remove rules and the cgroup.

        Idempotent. Rules are only removed once the group is empty, so
        an unstoppable process keeps its fail-closed rules.
        """
        if self._dns_relay is not None:
            self._dns_relay.stop()
            self._dns_relay = None

        if self.cgroup.exists():
            self.cgroup.kill_lingering()

        if self._installed:
            if self.cgroup.exists() and not self.cgroup.is_empty():
                self.log.warning(
                    "processes remain in %s; keeping nft rules (fail-closed)",
                    self.cgroup.rel_path,
                )
            else:
                try:
                    self._run_nft(["-f", "-"], batch=self.build_remove_batch())
                except RoutingError as exc:
                    self.log.warning(
                        "could not remove routing rules: %s\n"
                        "remove manually with:\n%s",
                        exc, self.build_remove_batch(),
                    )
                self._installed = False

        if not self.cgroup.remove():
            self.log.warning(
                "cgroup %s not empty; left in place", self.cgroup.rel_path
            )

    # -- DNS relay -------------------------------------------------------------

    def start_dns_relay(self, inbound_port: int) -> None:
        """Start the UDP DNS relay on ``self.dns_port``."""
        from .dnsrelay import DnsRelay, RelayError

        relay = DnsRelay(
            inbound_port=inbound_port,
            resolver=self.resolver,
            port=self.dns_port,
            logger=get_logger("dnsrelay"),
        )
        try:
            relay.start()
        except RelayError as exc:
            raise RoutingError(str(exc)) from exc
        self._dns_relay = relay
        self.log.info(
            "dns relay on 127.0.0.1:%d feeding inbound %d via TCP",
            self.dns_port, inbound_port,
        )

    # -- abandoned-session handling ---------------------------------------------

    def sweep(self, listing: str) -> list[str]:
        """Delete chains of abandoned sessions (cgroup gone or empty).

        ``listing`` is a ``sudo nft -j list table`` report. Returns the
        swept rid list. The table is deleted only when nothing but
        ProxyRotate chains remains; the user's rules are never touched.
        """
        names = _extract_chain_names(listing)
        by_rid: dict[str, set[str]] = {}
        foreign_chains: set[str] = set()
        for name in names:
            match = _CHAIN_RE.match(name)
            if match:
                by_rid.setdefault(match.group(1), set()).add(name)
            else:
                foreign_chains.add(name)
        if not by_rid:
            return []

        stale = sorted(rid for rid in by_rid if self._session_abandoned(rid))
        if not stale:
            return []

        removed: set[str] = set()
        for rid in stale:
            batch = self.build_remove_batch([rid])
            try:
                self._run_nft(["-f", "-"], batch=batch)
            except RoutingError as exc:
                # Batch is atomic; keep the other chains untouched.
                self.log.warning("could not sweep session %s: %s", rid, exc)
                continue
            removed.add(rid)
            self._remove_stale_cgroup_dir(rid)

        if removed and not foreign_chains \
                and not (set(by_rid) - removed):
            self._run_nft(
                ["-f", "-"], batch=f"delete table {NFT_FAMILY} {NFT_TABLE}\n"
            )
        return sorted(removed)

    def _session_abandoned(self, rid: str) -> bool:
        """Abandoned when its cgroup is gone or holds no processes."""
        cgroup = find_installed_cgroup(self.cgroup.root, rid)
        if cgroup is None or not cgroup.exists():
            return True
        return cgroup.pids() == []

    def _remove_stale_cgroup_dir(self, rid: str) -> None:
        cgroup = find_installed_cgroup(self.cgroup.root, rid)
        if cgroup is not None:
            with contextlib.suppress(OSError):
                cgroup.path.rmdir()
