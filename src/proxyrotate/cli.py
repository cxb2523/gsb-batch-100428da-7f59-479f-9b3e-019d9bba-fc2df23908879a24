"""Command line interface.

All ProxyRotate console output goes to stderr, keeping stdout pristine.
While the target application runs, ProxyRotate stays completely silent
on the terminal; details go to ~/.cache/proxyrotate/proxyrotate.log.
"""

from __future__ import annotations

import argparse
import contextlib
import signal
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__
from .config import (
    AppConfig,
    ConfigError,
    apply_cli_overrides,
    load_config,
)
from .health import check_proxies, healthy_only
from .logging import get_logger, setup_logging
from .models import Proxy
from .process import AppProcess, normalize_exit_code
from .proxies import load_proxies
from .rotation import ProxyPool, Rotator
from .routing import ProcessRouting, RoutingError
from .singbox import (
    ActivityMonitor,
    SingBoxError,
    SingBoxManager,
    _free_port,
    discover_binary,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NO_PROXIES = 3
EXIT_SINGBOX = 4
EXIT_COMMAND_NOT_FOUND = 127


class UsageFailure(Exception):
    pass


def say(message: str) -> None:
    """Lifecycle output for the human. Always stderr."""
    print(message, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxyrotate",
        description="Run a command through a rotating pool of proxies (via sing-box).",
        epilog=(
            "Everything after the command name is passed to the target "
            "unchanged; use -- to separate ambiguous arguments, e.g.\n"
            "  proxyrotate --every 30 -- opencode --continue"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="config file (default: ~/.config/proxyrotate/config.toml)",
    )
    parser.add_argument(
        "--proxy-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="proxy list file (default: ~/.config/proxyrotate/proxies.txt)",
    )
    parser.add_argument(
        "--sing-box",
        dest="sing_box",
        default=None,
        metavar="PATH",
        help="path to the sing-box executable",
    )
    parser.add_argument(
        "--every",
        type=float,
        default=None,
        metavar="SECONDS",
        help="rotate every SECONDS seconds (0 disables rotation; default: 60)",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=None,
        metavar="SECONDS",
        help="base cooldown for a failed proxy; doubles per repeat failure "
        "up to cooldown_max (default: 30)",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=None,
        metavar="N",
        help="rotate after N new proxied connections (0 disables); "
        "combines with --every as whichever comes first",
    )
    parser.add_argument(
        "--mb",
        type=float,
        default=None,
        metavar="MB",
        help="rotate after MB megabytes flowed through the proxy; "
        "combines with --every/--requests",
    )
    parser.add_argument(
        "--process-name",
        default=None,
        metavar="NAME",
        help="route this command and every process it creates through the "
        "proxy at the OS level (Linux only: cgroup v2 + nftables, needs "
        "sudo for the firewall rules); NAME must match the launched "
        "program name (see --process-name-mode)",
    )
    parser.add_argument(
        "--process-name-mode",
        choices=("exact", "substring"),
        default=None,
        metavar="MODE",
        help="how NAME matches the program: exact basename (default) or substring",
    )
    parser.add_argument(
        "--process-dns",
        default=None,
        metavar="HOST[:PORT]",
        help="plain-DNS resolver reached through the proxy for transparent "
        "name resolution (default: 1.1.1.1)",
    )
    parser.add_argument(
        "--health-check",
        dest="health_check",
        action="store_true",
        default=None,
        help="test proxies before starting (default)",
    )
    parser.add_argument(
        "--no-health-check",
        dest="health_check",
        action="store_false",
        help="skip the initial health check",
    )
    parser.add_argument(
        "--check-proxies",
        action="store_true",
        help="with no command: test proxies and exit; with a command: "
        "same as --health-check",
    )
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="per-proxy health check timeout (default: 5)",
    )
    parser.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error"),
        default=None,
        help="file log verbosity (default: info)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        metavar="COMMAND",
        help="command to run, with its arguments",
    )
    return parser


def merge_configuration(args: argparse.Namespace) -> AppConfig:
    try:
        config = load_config(args.config)
        overrides = {
            key: getattr(args, key)
            for key in (
                "proxy_file",
                "sing_box",
                "every",
                "cooldown",
                "health_timeout",
                "log_level",
                "process_name",
                "process_name_mode",
                "process_dns",
            )
        }
        apply_cli_overrides(
            config,
            proxy_file=overrides["proxy_file"],
            sing_box=overrides["sing_box"],
            every=overrides["every"],
            cooldown=overrides["cooldown"],
            health_check=args.health_check,
            health_timeout=overrides["health_timeout"],
            log_level=overrides["log_level"],
            rotation_requests=args.requests,
            rotation_mb=args.mb,
            process_name=overrides["process_name"],
            process_name_mode=overrides["process_name_mode"],
            process_dns=overrides["process_dns"],
        )

        if args.check_proxies and args.health_check is None:
            config.health_check = True

        if args.every is not None and args.every < 0:
            raise UsageFailure("--every must be >= 0")
        if args.cooldown is not None and args.cooldown < 0:
            raise UsageFailure("--cooldown must be >= 0")
        if args.health_timeout is not None and args.health_timeout <= 0:
            raise UsageFailure("--health-timeout must be positive")
        if args.requests is not None and args.requests < 1:
            raise UsageFailure("--requests must be >= 1")
        if args.mb is not None and args.mb <= 0:
            raise UsageFailure("--mb must be > 0")

        return config
    except ConfigError as exc:
        raise UsageFailure(str(exc)) from exc


def build_strategy(config: AppConfig):
    """Combine the enabled rotation strategies (whichever fires first)."""
    from .rotation import (
        BytesRotationStrategy,
        CombinedStrategy,
        ConnectionsRotationStrategy,
        TimeRotationStrategy,
    )

    parts = []
    if config.rotation_interval > 0:
        parts.append(TimeRotationStrategy(interval=config.rotation_interval))
    if config.rotation_requests:
        parts.append(ConnectionsRotationStrategy(max_connections=config.rotation_requests))
    if config.rotation_mb:
        max_bytes = int(config.rotation_mb * 1024 * 1024)
        parts.append(BytesRotationStrategy(max_bytes=max_bytes))

    if not parts:
        return TimeRotationStrategy(interval=0)
    if len(parts) == 1:
        return parts[0]
    return CombinedStrategy(*parts)


def needs_activity_monitor(config: AppConfig) -> bool:
    return bool(config.rotation_requests or config.rotation_mb)


# --------------------------------------------------------------------------- #
# proxy loading / checking
# --------------------------------------------------------------------------- #


def collect_proxies(config: AppConfig) -> list[Proxy]:
    try:
        result = load_proxies(config.proxy_file)
    except FileNotFoundError as exc:
        say(f"ERROR: {exc}")
        raise SystemExit(EXIT_USAGE) from exc
    except ValueError as exc:
        say(f"ERROR: {exc}")
        raise SystemExit(EXIT_USAGE) from exc

    log = get_logger("cli")
    for lineno, raw, reason in result.invalid:
        message = f"ignoring invalid proxy on line {lineno}: {reason}"
        say(f"WARNING: {message}")
        log.warning("%s (%r)", message, raw)

    if result.duplicates:
        log.info("ignored %d duplicate entr%s",
                 result.duplicates, "y" if result.duplicates == 1 else "ies")

    if not result.proxies:
        say(f"ERROR: no valid proxies found in {config.proxy_file}")
        raise SystemExit(EXIT_NO_PROXIES)

    say(f"Loaded {len(result.proxies)} proxies")
    return result.proxies


def run_health_check(
    proxies: list[Proxy], config: AppConfig, *, verbose: bool
) -> list[Proxy]:
    """Check all proxies; returns the healthy ones (fastest first)."""
    say(f"Checking {len(proxies)} proxies...")
    get_logger("cli").info("health check started (%d proxies)", len(proxies))

    counter = {"done": 0}

    def on_result(result) -> None:
        counter["done"] += 1
        if not verbose:
            if counter["done"] % 10 == 0 or counter["done"] == len(proxies):
                say(f"  checked {counter['done']}/{len(proxies)}")
            return
        mark = "OK  " if result.ok else "FAIL"
        detail = f"{result.latency_ms:.0f} ms" if result.ok else (result.error or "failed")
        say(f"  [{mark}] {result.proxy.redacted_url():<50} {detail}")

    results = check_proxies(
        proxies,
        timeout=config.health_timeout,
        on_result=on_result,
    )
    healthy = healthy_only(results)

    say(f"Healthy proxies: {len(healthy)}/{len(proxies)}")
    get_logger("cli").info("health check finished: %d/%d healthy",
                           len(healthy), len(proxies))
    return healthy


# --------------------------------------------------------------------------- #
# run session
# --------------------------------------------------------------------------- #


def validate_program_name(name: str, *, mode: str, program: str) -> None:
    """Guard against launching a different program than announced.

    Exact mode compares the basename of the launched program with NAME;
    substring mode checks containment. Wrong names abort before any
    routing is set up, so nothing is silently routed.
    """
    matched = name in program if mode == "substring" else name == program
    if not matched:
        say(
            f"ERROR: --process-name {name!r} does not match the program to run "
            f"({program!r}); adjust the name or use --process-name-mode substring"
        )
        raise SystemExit(EXIT_USAGE)


def create_routing(config: AppConfig, *, redirect_port: int,
                   dns_port: int) -> ProcessRouting:
    """Build the routing session for one run (test seam: monkeypatchable)."""
    return ProcessRouting(
        name=config.process_name,
        mode=config.process_name_mode,
        resolver=config.process_dns,
        redirect_port=redirect_port,
        dns_port=dns_port,
    )


def resolve_routing(command: list[str], config: AppConfig) -> ProcessRouting | None:
    """Validate and realize process routing settings, or return None.

    Runs before anything expensive so a wrong platform or name fails
    immediately with a clear message and nothing is routed.
    """
    if not config.process_name:
        return None

    if sys.platform != "linux":
        say(
            "ERROR: --process-name requires Linux (cgroup v2 + nftables). "
            "On this platform ProxyRotate can only inject proxy environment "
            "variables."
        )
        raise SystemExit(EXIT_USAGE)

    program = Path(command[0]).name
    validate_program_name(
        config.process_name, mode=config.process_name_mode, program=program
    )
    routing = create_routing(
        config, redirect_port=_free_port(), dns_port=_free_port()
    )
    log = get_logger("session")
    log.info(
        "process routing enabled: program %r (mode %s), resolver %s, "
        "redirect port %d, dns port %d",
        program, routing.mode, config.process_dns,
        routing.redirect_port, routing.dns_port,
    )
    return routing


def run_session(command: list[str], config: AppConfig) -> int:
    log = get_logger("session")

    routing = resolve_routing(command, config)

    proxies = collect_proxies(config)

    if config.health_check:
        healthy = run_health_check(proxies, config, verbose=False)
        if not healthy:
            say("ERROR: no usable proxies after health check")
            return EXIT_NO_PROXIES
        proxies = healthy

    try:
        binary = discover_binary(config.sing_box)
    except SingBoxError as exc:
        say(f"ERROR: {exc}")
        return EXIT_SINGBOX

    def discard_routing() -> None:
        """Release routing artifacts before handing control over/succeeding.

        Used when sing-box fails before the application ever starts:
        either nothing exists yet (teardown is a no-op) or the cgroup
        was created and must not leak.
        """
        if routing is None:
            return
        try:
            routing.teardown()
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask
            log.warning("routing teardown failed: %s", exc)

    try:
        if routing is not None:
            say("Routing the launched process tree via cgroup + nftables")
            routing.prepare()
        manager = SingBoxManager(
            proxies,
            binary=binary,
            redirect_port=routing.redirect_port if routing else None,
        )
        manager.start()
    except RoutingError as exc:
        say(f"ERROR: routing setup failed: {exc}")
        discard_routing()
        return EXIT_FAILURE
    except SingBoxError as exc:
        say(f"ERROR: {exc}")
        discard_routing()
        return EXIT_SINGBOX

    pool = ProxyPool(proxies, cooldown=config.failure_cooldown,
                     cooldown_cap=config.cooldown_max)
    strategy = build_strategy(config)

    activity_monitor = None
    if needs_activity_monitor(config):
        activity_monitor = ActivityMonitor(manager)
        log.info("activity tracking enabled (requests=%s mb=%s)",
                 config.rotation_requests, config.rotation_mb)

    rotator = Rotator(manager, pool, strategy, activity_monitor=activity_monitor)

    app = AppProcess(
        command,
        env_overrides=manager.env_for_child(),
        cgroup_procs=routing.cgroup.procs_path if routing else None,
    )
    stop_event = threading.Event()

    def request_shutdown(signum, _frame) -> None:
        # File logging only: the terminal belongs to the application.
        log.debug("received signal %s", signal.Signals(signum).name)
        stop_event.set()
        if signum == signal.SIGTERM:
            # Only the parent receives SIGTERM; forward it. On SIGINT
            # the whole foreground group already got it — terminating
            # again would race the application's own handling.
            app.request_stop()

    previous_handlers = {}
    try:
        chosen = rotator.select_initial()
        if chosen is None:
            say("ERROR: no usable proxies (all failed verification)")
            return EXIT_NO_PROXIES
        log.info("initial proxy index %d", chosen)

        if routing is not None:
            # Order is fail-closed: relay first (so packets cannot arrive
            # on an empty slot), then rules, then the application joins
            # the cgroup on exec. Any failure here never starts the app.
            routing.start_dns_relay(inbound_port=manager.inbound_port)
            routing.install()

        say(f"Starting {' '.join(command)}")
        try:
            app.start()
        except FileNotFoundError:
            program = Path(command[0]).name
            say(f"ERROR: command not found: {program}")
            return EXIT_COMMAND_NOT_FOUND
        except PermissionError:
            say(f"ERROR: cannot execute: {command[0]}")
            return EXIT_COMMAND_NOT_FOUND
        except OSError as exc:
            # e.g. a failing pre-exec hook (cgroup join) on Linux.
            say(f"ERROR: cannot execute: {command[0]} ({exc})")
            return EXIT_FAILURE

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError):  # non-main thread
                previous_handlers[sig] = signal.signal(sig, request_shutdown)

        rotator.run(app_running=lambda: app.running, stop_event=stop_event)

        returncode = app.wait_and_finalize()
    except SingBoxError as exc:
        say(f"ERROR: {exc}")
        return EXIT_SINGBOX
    except RoutingError as exc:
        # Late failures (relay bind, rules install): the finally block
        # tears the routing and sing-box down; the app never starts.
        say(f"ERROR: routing setup failed: {exc}")
        return EXIT_FAILURE
    finally:
        for sig, handler in previous_handlers.items():
            with contextlib.suppress(ValueError):
                signal.signal(sig, handler)
        if routing is not None:
            try:
                # Order: stop the relay, stop the group's stragglers,
                # remove rules, remove the cgroup. The direct child is
                # already gone (waited above); descendants die here.
                # Everything is best-effort and logged.
                routing.teardown()
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask
                log.warning("routing teardown failed: %s", exc)
        manager.stop()

    exit_code = normalize_exit_code(returncode)
    name = Path(command[0]).name
    if exit_code == 128 + signal.SIGINT:
        say("Interrupted")
    else:
        say(f"{name} exited with code {exit_code}")
    say("ProxyRotate stopped")
    return exit_code


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def check_only(config: AppConfig) -> int:
    proxies = collect_proxies(config)
    healthy = run_health_check(proxies, config, verbose=True)
    return EXIT_OK if healthy else EXIT_NO_PROXIES


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]

    try:
        config = merge_configuration(args)
    except UsageFailure as exc:
        parser.error(str(exc))
        return EXIT_USAGE  # pragma: no cover - parser.error exits

    setup_logging(config.log_level)
    log = get_logger("cli")
    log.info("proxyrotate %s starting", __version__)

    try:
        if not command:
            if args.check_proxies:
                return check_only(config)
            parser.error("missing COMMAND (example: proxyrotate opencode)")

        say(f"ProxyRotate {__version__}")
        return run_session(command, config)

    except KeyboardInterrupt:
        say("\nInterrupted")
        say("ProxyRotate stopped")
        return 130
    except SingBoxError as exc:
        say(f"ERROR: {exc}")
        return EXIT_SINGBOX
    except subprocess.SubprocessError as exc:
        say(f"ERROR: process failure: {exc}")
        return EXIT_FAILURE
    finally:
        log.info("proxyrotate exited")


if __name__ == "__main__":
    sys.exit(main())
