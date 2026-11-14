import asyncio
import time

pause_event = asyncio.Event()
pause_event.set()  # starts unpaused

_node_statuses: dict[str, str] = {}
_node_outputs: dict[str, object] = {}
_node_logs: dict[str, list] = {}
_sse_queues: list[asyncio.Queue] = []

# Optional listener notified when the generated workflow reports a terminal
# status on the synthetic "__workflow__" node. The backend uses it to tell a
# logically-finished run (which then holds the browser session open) apart from
# one that is still executing, so queued triggers can start right away.
_workflow_listener = None


def set_workflow_listener(fn) -> None:
    global _workflow_listener
    _workflow_listener = fn


def report_node(node_id: str, status: str) -> None:
    _node_statuses[node_id] = status
    for q in _sse_queues:
        q.put_nowait({"node_id": node_id, "status": status})
    if node_id == "__workflow__" and _workflow_listener is not None:
        try:
            _workflow_listener(status)
        except Exception:
            pass  # a listener hiccup must never break the workflow


def report_node_output(node_id: str, output: object) -> None:
    _node_outputs[node_id] = output
    for q in _sse_queues:
        q.put_nowait({"type": "node_output", "node_id": node_id, "output": output})


def report_node_log(node_id: str, line: str) -> None:
    entry = {"t": round(time.time() * 1000), "msg": str(line)[:600]}
    if node_id not in _node_logs:
        _node_logs[node_id] = []
    _node_logs[node_id].append(entry)
    if len(_node_logs[node_id]) > 100:
        _node_logs[node_id] = _node_logs[node_id][-100:]
    for q in _sse_queues:
        q.put_nowait({"type": "node_log", "node_id": node_id, "entry": entry})


def reset_execution() -> None:
    _node_statuses.clear()
    _node_outputs.clear()
    _node_logs.clear()
    for q in _sse_queues:
        q.put_nowait({"type": "reset"})


def get_node_statuses() -> dict:
    return dict(_node_statuses)


def get_node_outputs() -> dict:
    return dict(_node_outputs)


def get_node_logs() -> dict:
    return {k: list(v) for k, v in _node_logs.items()}
