"""Ordered batch execution with per-step settlement over AgentInBrowser.

Design goals (see browser/batch_scraping/README.md):

* Each step is settled independently as ``success`` / ``failed`` / ``skipped``.
* A failed step never aborts the batch; steps that depend on a failed (or
  skipped) step are themselves marked *skipped* instead of reporting a
  misleading success.
* Index based steps (``click`` / ``send_keys`` / ``get_html``) automatically
  re-run their most recent finder once when the cached index is stale, then
  retry exactly once.  A second failure is recorded as a hard failure.
* Window switches / closes invalidate the element cache explicitly: the
  remembered finder is dropped, so later index steps force a fresh lookup.
* Successfully scraped text is kept in a result context and in the step
  record, never discarded because an unrelated later step fails.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Sequence

from .client import AIBClient, BrowserAPIError

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Commands that (re)populate the server-side element cache.
FINDER_CMDS = ("find", "inputs", "buttons")
# Commands that consume a cached element by index.
INDEXED_CMDS = ("click", "send_keys", "get_html")

# Error text fragments that indicate a recoverable, cache-related failure.
RECOVERABLE_FRAGMENTS = (
    "stale",
    "index",
    "no elements cached",
    "element cache",
    "element not found",
    "no element",
    "not found in cache",
)

_VAR_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_.]*)\}")


class BatchRunner:
    """Execute a list of step dicts against an :class:`AIBClient`.

    A step is a plain dict, e.g.::

        {"op": "find", "selector": "button.open", "name": "find-open"}
        {"op": "click", "index": 0}
        {"op": "get_text", "store": "body"}
        {"op": "switch_window", "index": -1}
        {"op": "close_window"}
        {"op": "send_keys", "index": 1, "text": "${username}"}

    Parameters
    ----------
    client:
        A configured :class:`AIBClient`.
    max_retries:
        How many recovery retries are allowed for a stale index (default 1,
        matching the "重新查找一次再重试" requirement).
    """

    def __init__(self, client: AIBClient, max_retries: int = 1):
        self.client = client
        self.max_retries = max_retries
        self._reset_state()

    def _reset_state(self) -> None:
        self.records: List[Dict[str, Any]] = []
        self.context: Dict[str, Any] = {}
        # Most recent finder step within the *current* window.
        self._last_finder: Optional[Dict[str, Any]] = None
        # name -> step seq, for explicit depends_on and ${var} lookups.
        self._name_to_seq: Dict[str, int] = {}
        # var name -> seq of the step that stores it.
        self._var_to_seq: Dict[str, int] = {}
        self._status_by_seq: Dict[int, str] = {}

    # ------------------------------------------------------------------ DSL
    @staticmethod
    def _step_cmd(step: Dict[str, Any]) -> str:
        return str(step["op"]).lower()

    def _finder_spec(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the /execute params needed to replay a finder step."""
        cmd = self._step_cmd(step)
        if cmd == "find":
            return {"cmd": "find", "params": {"selector": step["selector"]}}
        return {"cmd": cmd, "params": {}}

    @staticmethod
    def _is_recoverable(exc: BrowserAPIError) -> bool:
        msg = exc.message.lower()
        return any(fragment in msg for fragment in RECOVERABLE_FRAGMENTS)

    # --------------------------------------------------------- dependencies
    def _inferred_deps(self, seq: int, steps: Sequence[Dict[str, Any]]) -> List[int]:
        """Return dependency step seqs implied by the step's shape."""
        step = steps[seq]
        cmd = self._step_cmd(step)
        deps: List[int] = []

        # Index based steps need the most recent finder in this window.
        if cmd in INDEXED_CMDS and self._last_finder is not None:
            deps.append(self._last_finder["seq"])

        # ${var} placeholders depend on the step that produced the variable.
        for value in step.values():
            if isinstance(value, str):
                for match in _VAR_RE.findall(value):
                    if match in self._var_to_seq:
                        deps.append(self._var_to_seq[match])

        # Explicit dependency by step name or seq.
        explicit = step.get("depends_on")
        if explicit is not None:
            if isinstance(explicit, (str, int)):
                explicit = [explicit]
            for ref in explicit:
                if isinstance(ref, int):
                    deps.append(ref)
                elif ref in self._name_to_seq:
                    deps.append(self._name_to_seq[ref])
        return sorted(set(deps))

    def _blocked_by(self, deps: Sequence[int]) -> Optional[List[int]]:
        """Return the dependency seqs that did not succeed, if any."""
        bad = [d for d in deps if self._status_by_seq.get(d) != STATUS_SUCCESS]
        return bad or None

    # ------------------------------------------------------------- variable
    def _substitute(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        full_match = _VAR_RE.fullmatch(value.strip())
        if full_match:
            # Whole-value substitution preserves non-string types.
            name = full_match.group(1)
            if name not in self.context:
                raise KeyError(name)
            return self.context[name]

        def repl(match: re.Match) -> str:
            name = match.group(1)
            if name not in self.context:
                raise KeyError(name)
            return str(self.context[name])

        return _VAR_RE.sub(repl, value)

    # ------------------------------------------------------------ execution
    def run_batch(self, steps: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Run all steps and return a settlement report dict."""
        self._reset_state()

        # Register every variable producer up front: a consumer of a failed
        # producer is then SKIPPED via dependency tracking, while a truly
        # undeclared ${var} still surfaces as a missing-variable failure.
        for seq, step in enumerate(steps):
            if step.get("store"):
                self._var_to_seq[step["store"]] = seq
            if step.get("name"):
                self._name_to_seq[step["name"]] = seq

        for seq, step in enumerate(steps):
            deps = self._inferred_deps(seq, steps)
            blocked = self._blocked_by(deps)
            if blocked is not None:
                self._record(seq, step, STATUS_SKIPPED,
                             reason=f"dependency not successful: seqs {blocked}",
                             skipped_due_to=blocked)
                continue

            try:
                self._execute_step(seq, step)
            except BrowserAPIError as exc:
                self._record(seq, step, STATUS_FAILED, error=exc.message,
                             reason=self._classify_reason(exc.message))
            except KeyError as exc:
                self._record(seq, step, STATUS_FAILED,
                             error=f"missing variable {exc}",
                             reason="missing-variable")
            except Exception as exc:  # defensive: never kill the whole batch
                self._record(seq, step, STATUS_FAILED,
                             error=f"{type(exc).__name__}: {exc}",
                             reason="unexpected-error")

        return self.build_report()

    def _execute_step(self, seq: int, step: Dict[str, Any]) -> None:
        cmd = self._step_cmd(step)
        started = time.monotonic()
        attempts = 0
        retried = False

        if cmd in FINDER_CMDS:
            spec = self._finder_spec(step)
            data = self.client.execute(spec["cmd"], **spec["params"])
            attempts += 1
            # Remember the finder so index steps can replay it after a
            # stale-cache failure. Window switches clear this reference.
            self._last_finder = {"seq": seq, "spec": spec}
            captured = self._store_if_requested(step, data)
            self._record(seq, step, STATUS_SUCCESS, attempts=attempts,
                         data=self._summarise_finder(data), captured=captured,
                         duration_ms=self._elapsed(started))
            return

        if cmd in INDEXED_CMDS:
            self._run_indexed(seq, step, started)
            return

        # Non-indexed commands.
        data = self._run_plain(cmd, step)
        attempts += 1

        if cmd == "switch_window":
            # A new window has its own DOM and its own element cache.
            self._last_finder = None
        elif cmd == "close_window":
            # Server returns focus to a remaining window; its cache is stale
            # relative to anything we remember from the closed window.
            self._last_finder = None

        captured = self._store_if_requested(step, data)
        self._record(seq, step, STATUS_SUCCESS, attempts=attempts,
                     retried=retried, data=data, captured=captured,
                     duration_ms=self._elapsed(started))

    def _run_plain(self, cmd: str, step: Dict[str, Any]) -> Dict[str, Any]:
        if cmd == "get_text":
            return self.client.execute("get_text")
        if cmd == "page_info":
            return self.client.execute("page_info")
        if cmd == "sleep":
            return self.client.execute("sleep", seconds=step.get("seconds", 1))
        if cmd == "execute_script":
            return self.client.execute(
                "execute_script", script=self._substitute(step["script"])
            )
        if cmd == "switch_window":
            return self.client.execute(
                "switch_window",
                index=self._substitute(step.get("index", -1)),
            )
        if cmd == "close_window":
            return self.client.execute("close_window")
        raise BrowserAPIError(f"unknown step op: {cmd}")

    def _run_indexed(self, seq: int, step: Dict[str, Any], started: float) -> None:
        cmd = self._step_cmd(step)
        attempts = 0
        retried = False

        def invoke() -> Dict[str, Any]:
            index = self._substitute(step["index"])
            if cmd == "click":
                return self.client.execute("click", index=index)
            if cmd == "send_keys":
                return self.client.execute(
                    "send_keys", index=index, text=self._substitute(step["text"])
                )
            # get_html
            return self.client.execute("get_html", index=index)

        try:
            data = invoke()
            attempts += 1
        except BrowserAPIError as exc:
            attempts += 1
            recoverable = self._is_recoverable(exc)
            can_replay = self._last_finder is not None
            if not (recoverable and can_replay and self.max_retries > 0):
                raise
            # Recovery: re-run the finder to rebuild the cache, then retry
            # the indexed operation exactly once.
            retried = True
            spec = self._last_finder["spec"]
            try:
                re_found = self.client.execute(spec["cmd"], **spec["params"])
            except BrowserAPIError as re_find_exc:
                # The element really is gone: record as a hard failure.
                self._record(
                    seq, step, STATUS_FAILED, attempts=attempts, retried=True,
                    error=(f"index failed: {exc.message}; re-find also failed: "
                           f"{re_find_exc.message}"),
                    reason=self._classify_reason(re_find_exc.message),
                    duration_ms=self._elapsed(started),
                )
                return
            if re_found.get("count", 0) == 0:
                # Re-find succeeded but the element no longer exists.
                self._record(
                    seq, step, STATUS_FAILED, attempts=attempts, retried=True,
                    error=(f"index failed: {exc.message}; re-find returned 0 "
                           f"elements for {spec['cmd']} "
                           f"{spec['params'].get('selector', '')}".strip()),
                    reason="element-not-found",
                    duration_ms=self._elapsed(started),
                )
                return
            try:
                data = invoke()
                attempts += 1
            except BrowserAPIError:
                raise

        captured = self._store_if_requested(step, data)
        self._record(seq, step, STATUS_SUCCESS, attempts=attempts,
                     retried=retried, data=data, captured=captured,
                     duration_ms=self._elapsed(started))

    # ------------------------------------------------------------- storage
    def _store_if_requested(self, step: Dict[str, Any],
                            data: Dict[str, Any]) -> Optional[Any]:
        var = step.get("store")
        if not var:
            return None
        cmd = self._step_cmd(step)
        if cmd == "get_text":
            value: Any = data.get("text", "")
        elif cmd == "get_html":
            value = data.get("html", "")
        elif cmd == "execute_script":
            value = data.get("result")
        else:
            value = data
        self.context[var] = value
        return value

    # -------------------------------------------------------------- record
    def _record(self, seq: int, step: Dict[str, Any], status: str,
                *, attempts: int = 0, retried: bool = False,
                data: Optional[Dict[str, Any]] = None,
                captured: Any = None,
                error: Optional[str] = None,
                reason: Optional[str] = None,
                skipped_due_to: Optional[List[int]] = None,
                duration_ms: Optional[int] = None) -> None:
        if duration_ms is None:
            duration_ms = 0
        record = {
            "seq": seq,
            "name": step.get("name"),
            "op": self._step_cmd(step),
            "status": status,
            "attempts": attempts,
            "retried": retried,
            "reason": reason,
            "error": error,
            "skipped_due_to": skipped_due_to or [],
            "data": data,
            "captured": captured,
            "duration_ms": duration_ms,
        }
        self.records.append(record)
        self._status_by_seq[seq] = status

    @staticmethod
    def _elapsed(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    @staticmethod
    def _summarise_finder(data: Dict[str, Any]) -> Dict[str, Any]:
        """Trim finder payloads so the report stays readable."""
        summary: Dict[str, Any] = {"count": data.get("count", 0)}
        for key in ("elements", "inputs", "buttons"):
            if key in data:
                summary[key] = data[key][:5]
                if data.get("count", 0) > 5:
                    summary["truncated"] = True
                break
        return summary

    @staticmethod
    def _classify_reason(message: str) -> str:
        msg = message.lower()
        if "stale" in msg:
            return "stale-index"
        if "no elements cached" in msg or "cache" in msg:
            return "empty-element-cache"
        if "not found" in msg or "no element" in msg:
            return "element-not-found"
        if "window" in msg:
            return "window-error"
        if "cannot reach" in msg:
            return "server-unreachable"
        return "command-error"

    # -------------------------------------------------------------- report
    def build_report(self) -> Dict[str, Any]:
        counts = {STATUS_SUCCESS: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0}
        failure_reasons: Dict[str, Dict[str, Any]] = {}
        for record in self.records:
            counts[record["status"]] += 1
            if record["status"] == STATUS_FAILED:
                key = record["reason"] or "unknown"
                bucket = failure_reasons.setdefault(
                    key, {"reason": key, "count": 0, "seqs": [], "sample_error": record["error"]}
                )
                bucket["count"] += 1
                bucket["seqs"].append(record["seq"])

        return {
            "total": len(self.records),
            "summary": {
                "success": counts[STATUS_SUCCESS],
                "failed": counts[STATUS_FAILED],
                "skipped": counts[STATUS_SKIPPED],
            },
            "failure_reasons": list(failure_reasons.values()),
            "results": self.context,
            "steps": self.records,
        }
