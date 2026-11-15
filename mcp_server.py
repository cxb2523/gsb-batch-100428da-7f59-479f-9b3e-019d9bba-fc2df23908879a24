#!/usr/bin/env python3
"""MCP tools for agent-first competitive intelligence demos."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import collection_outcomes
import env_utils
import providers

env_utils.load_env()
PROJECT_ROOT = Path(__file__).parent


try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # Allows direct smoke tests before installing mcp[cli].
    FastMCP = None


class _ToolRegistry:
    """Tiny decorator-compatible registry used when MCP is not installed."""

    def __init__(self, name: str):
        self.name = name
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator

    def run(self, *args, **kwargs):
        raise RuntimeError("Install mcp[cli]>=1.27,<2 to run the MCP server")


mcp = FastMCP("Competitive Intelligence Agent Tools") if FastMCP else _ToolRegistry(
    "Competitive Intelligence Agent Tools"
)


def _normalize_competitors(competitors: str | list[str] | tuple[str, ...] | None) -> str | None:
    """Accept MCP-friendly comma strings or JSON arrays for live competitor overrides."""
    if competitors is None:
        return None
    if isinstance(competitors, str):
        values = competitors.split(",")
    else:
        values = competitors
    normalized = [value.strip() for value in values if str(value).strip()]
    return ",".join(normalized) if normalized else None


def _apply_live_overrides(
    env: dict[str, str],
    competitors: str | list[str] | tuple[str, ...] | None = None,
    event_query: str | None = None,
    max_results: int | None = None,
    search_days_back: int | None = None,
    refresh_interval_seconds: int | None = None,
) -> dict[str, str]:
    competitor_list = _normalize_competitors(competitors)
    if competitor_list:
        env["COMPETITORS"] = competitor_list
    if event_query:
        env["EVENT_QUERY"] = event_query
    if max_results is not None:
        env["MAX_RESULTS_PER_COMPETITOR"] = str(max_results)
    if search_days_back is not None:
        env["SEARCH_DAYS_BACK"] = str(search_days_back)
    if refresh_interval_seconds is not None:
        env["REFRESH_INTERVAL_SECONDS"] = str(refresh_interval_seconds)
    return env


def _live_effective_config(env: dict[str, str]) -> dict[str, str | None]:
    return {
        "competitors": env.get("COMPETITORS"),
        "event_query": env.get("EVENT_QUERY"),
        "max_results_per_competitor": env.get("MAX_RESULTS_PER_COMPETITOR"),
        "search_days_back": env.get("SEARCH_DAYS_BACK"),
        "refresh_interval_seconds": env.get("REFRESH_INTERVAL_SECONDS"),
    }


@mcp.tool()
def analyze_saved_articles(
    config_path: str | None = None,
    input_path: str | None = None,
    output_dir: str | None = None,
    slug: str | None = None,
    dashboard: bool = False,
) -> dict[str, Any]:
    """Analyze saved article JSON and write brief/event exports for agents."""
    provider = providers.LocalIntelProvider()
    return provider.analyze_saved_articles(
        config_path=config_path,
        input_path=input_path,
        output_dir=output_dir,
        slug=slug,
        dashboard=dashboard,
    )


@mcp.tool()
def search_events(
    mode: str = "local",
    query: str | None = None,
    competitor: str | None = None,
    event_type: str | None = None,
    significance: str | None = None,
    limit: int = 20,
    config_path: str | None = None,
    input_path: str | None = None,
    events_path: str | None = None,
) -> dict[str, Any]:
    """Search competitive events from local sample data or CocoIndex/Postgres."""
    provider = providers.get_provider(mode)
    if mode == "cocoindex":
        return provider.search_events(
            query=query,
            competitor=competitor,
            event_type=event_type,
            significance=significance,
            limit=limit,
        )
    return provider.search_events(
        query=query,
        competitor=competitor,
        event_type=event_type,
        significance=significance,
        limit=limit,
        config_path=config_path,
        input_path=input_path,
        events_path=events_path,
    )


@mcp.tool()
def get_trending_competitors(
    mode: str = "local",
    days: int = 7,
    limit: int = 10,
    config_path: str | None = None,
    input_path: str | None = None,
    events_path: str | None = None,
) -> dict[str, Any]:
    """Rank competitors by current intelligence activity."""
    provider = providers.get_provider(mode)
    if mode == "cocoindex":
        return provider.get_trending_competitors(days=days, limit=limit)
    return provider.get_trending_competitors(
        limit=limit,
        config_path=config_path,
        input_path=input_path,
        events_path=events_path,
    )


@mcp.tool()
def create_brief(
    mode: str = "local",
    config_path: str | None = None,
    input_path: str | None = None,
    events_path: str | None = None,
    output_dir: str | None = None,
    slug: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Create a Markdown intelligence brief for an agent or analyst."""
    provider = providers.get_provider(mode)
    if mode == "cocoindex":
        return provider.create_brief(
            config_path=config_path,
            output_dir=output_dir,
            slug=slug,
            limit=limit,
        )
    return provider.create_brief(
        config_path=config_path,
        input_path=input_path,
        events_path=events_path,
        output_dir=output_dir,
        slug=slug,
    )


@mcp.tool()
def create_dashboard(
    mode: str = "local",
    config_path: str | None = None,
    input_path: str | None = None,
    events_path: str | None = None,
    output_dir: str | None = None,
    slug: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Create a static HTML dashboard from competitive intelligence events."""
    provider = providers.get_provider(mode)
    if mode == "cocoindex":
        return provider.create_dashboard(
            config_path=config_path,
            output_dir=output_dir,
            slug=slug,
            limit=limit,
        )
    return provider.create_dashboard(
        config_path=config_path,
        input_path=input_path,
        events_path=events_path,
        output_dir=output_dir,
        slug=slug,
    )


def _configured_competitors(env: dict[str, str]) -> list[str]:
    """Return the competitor names this run will create search sources for."""
    raw = env.get("COMPETITORS") or ""
    return [name.strip() for name in raw.split(",") if name.strip()]


def _read_run_outcomes(
    outcome_dir: str | Path | None, competitors: list[str]
) -> list[dict[str, Any]]:
    """Load per-competitor outcomes written by the child process.

    Competitors with no record are surfaced as ``not_run`` so a partial or
    aborted run is distinguishable from a clean one.
    """
    recorded = collection_outcomes.read_outcomes(outcome_dir)
    return collection_outcomes.merge_with_expected(recorded, competitors)


def _outcome_fields(
    outcomes: list[dict[str, Any]],
    outcome_dir: str | Path | None,
    summary_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the per-competitor fields merged into the tool's return value."""
    summary = collection_outcomes.summarize_outcomes(outcomes)
    failed = [
        outcome["competitor"]
        for outcome in outcomes
        if outcome["status"] == collection_outcomes.STATUS_FAILED
    ]
    empty = [
        outcome["competitor"]
        for outcome in outcomes
        if outcome["status"] == collection_outcomes.STATUS_EMPTY
    ]
    not_run = [
        outcome["competitor"]
        for outcome in outcomes
        if outcome["status"] == collection_outcomes.STATUS_NOT_RUN
    ]
    return {
        "competitors": outcomes,
        "collection_summary": summary,
        "failed_competitors": failed,
        "empty_competitors": empty,
        "not_run_competitors": not_run,
        "collection_complete": summary["total"] > 0 and not failed and not not_run,
        "outcome_dir": str(outcome_dir) if outcome_dir else None,
        "outcome_file": str(summary_path) if summary_path else None,
    }


def _cocoindex_command(force: bool, watch: bool) -> list[str]:
    """Build the ``cocoindex update`` command for this interpreter's install."""
    candidates = [Path(sys.executable).with_name("cocoindex")]
    if os.name == "nt":
        candidates.append(Path(sys.executable).with_name("cocoindex.exe"))
    binary = next((str(candidate) for candidate in candidates if candidate.exists()), None)
    binary = binary or shutil.which("cocoindex") or "cocoindex"

    command = [binary, "update"]
    if watch:
        command.extend(["-L", "main.py"])
    else:
        command.append("main")
        if force:
            command.append("-f")
    return command


@mcp.tool()
def run_cocoindex_update(
    live: bool = False,
    watch: bool = False,
    force: bool = True,
    competitors: str | list[str] | None = None,
    event_query: str | None = None,
    max_results: int | None = None,
    search_days_back: int | None = None,
    refresh_interval_seconds: int | None = None,
) -> dict[str, Any]:
    """Run the live CocoIndex update path when explicitly enabled."""
    env = _apply_live_overrides(
        os.environ.copy(),
        competitors=competitors,
        event_query=event_query,
        max_results=max_results,
        search_days_back=search_days_back,
        refresh_interval_seconds=refresh_interval_seconds,
    )
    effective_config = _live_effective_config(env)
    configured_competitors = _configured_competitors(env)

    if not live:
        return {
            "ok": False,
            "mode": "cocoindex",
            "effective_config": effective_config,
            "message": "Set live=true to run CocoIndex because this can call APIs and write Postgres.",
        }

    missing = [
        key
        for key in ("COCOINDEX_DATABASE_URL", "TAVILY_API_KEY", "OPENAI_API_KEY")
        if not env.get(key)
    ]
    if missing:
        return {
            "ok": False,
            "mode": "cocoindex",
            "missing_env": missing,
            "effective_config": effective_config,
            "message": "Live CocoIndex mode requires database and API credentials.",
        }

    command = _cocoindex_command(force=force, watch=watch)

    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) if not pythonpath else f"{PROJECT_ROOT}{os.pathsep}{pythonpath}"
    )
    # The child process cannot hand structured data back through stdout, so it
    # writes one outcome record per competitor into a run-scoped directory that
    # stays on disk after the run for inspection.  An externally configured
    # COMPETITOR_OUTCOME_DIR is treated as the parent of run directories, so
    # records from an earlier run can never be attributed to this one.
    outcome_root = env.get(collection_outcomes.ENV_OUTCOME_DIR) or None
    outcome_dir = collection_outcomes.new_run_dir(outcome_root)
    env[collection_outcomes.ENV_OUTCOME_DIR] = str(outcome_dir)

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        # CocoIndex writes UTF-8 (progress bars, check marks).  Without an
        # explicit encoding, Windows decodes with the ANSI code page and the
        # reader thread dies, leaving stdout/stderr as None.
        encoding="utf-8",
        errors="replace",
        timeout=600,
        cwd=PROJECT_ROOT,
        env=env,
    )
    outcomes = _read_run_outcomes(outcome_dir, configured_competitors)
    summary_path = collection_outcomes.write_summary(outcomes, outcome_dir)
    return _update_result(
        completed,
        command,
        effective_config,
        outcomes,
        outcome_dir,
        summary_path,
        configured_competitors,
    )


def _update_result(
    completed: subprocess.CompletedProcess[str],
    command: list[str],
    effective_config: dict[str, str | None],
    outcomes: list[dict[str, Any]],
    outcome_dir: Path,
    summary_path: Path,
    configured_competitors: list[str],
) -> dict[str, Any]:
    """Assemble the update result without changing existing field semantics.

    ``ok`` still mirrors ``returncode == 0``.  Partial competitor failures show
    up as explicit ``status``/``partial``/``competitors`` fields instead of
    being folded into ``ok``, and are never reported as silent success.

    ``status`` values:
    - ``failed``   the CocoIndex update itself exited non-zero
    - ``partial``  the update completed but at least one competitor's request
                   failed, or a configured competitor produced no outcome at
                   all; the other competitors' data is unaffected
    - ``empty``    the update completed and no competitor returned usable news
    - ``success``  the update completed and at least one competitor collected

    ``not_run_competitors`` lists configured competitors without any record.
    It never counts as a collected competitor and is reported in ``message``,
    so an unrecorded competitor is never silently treated as a success.
    """
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "mode": "cocoindex",
        "command": command,
        "effective_config": effective_config,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "")[-4000:],
        "stderr": (completed.stderr or "")[-4000:],
    }
    result.update(_outcome_fields(outcomes, outcome_dir, summary_path))

    failed = result["failed_competitors"]
    not_run = result["not_run_competitors"]
    empty = result["empty_competitors"]
    summary = result["collection_summary"]

    if not result["ok"]:
        result["status"] = "failed"
    elif not configured_competitors:
        result["status"] = "empty"
    elif failed or not_run:
        result["status"] = "partial"
    elif summary["collected"] == 0:
        result["status"] = "empty"
    else:
        result["status"] = "success"
    result["partial"] = result["status"] == "partial"

    notes: list[str] = []
    if not configured_competitors:
        notes.append("No competitors were configured; nothing was collected.")
    if result["status"] == "failed":
        notes.append(
            f"CocoIndex update failed with returncode {completed.returncode}; "
            "per-competitor outcomes below may be incomplete."
        )
    if failed:
        notes.append("request failed: " + ", ".join(failed))
        notes.append(
            "a failed competitor contributed no rows this round, so articles "
            "previously indexed for it were removed while other competitors "
            "were unaffected; retry to repopulate"
        )
    if not_run:
        notes.append("no outcome recorded for: " + ", ".join(not_run))
    if empty and result["status"] in ("success", "empty"):
        notes.append("no usable results for: " + ", ".join(empty))
    if notes:
        result["message"] = "; ".join(note.rstrip(".") for note in notes) + "."
    return result


if __name__ == "__main__":
    mcp.run()
