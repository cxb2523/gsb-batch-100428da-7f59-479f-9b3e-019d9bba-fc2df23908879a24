"""Append-only event journal. Every kernel component writes here; nothing edits history.

One JSONL file per day. A torn final line (crash mid-write) is detected and quarantined on
read, never silently parsed. Event types are a closed set so a typo cannot invent a lane.
"""
import json, os, time, threading

EVENTS = {
    'frame_captured', 'scene_changed', 'intent_created', 'target_grounded',
    'action_authorized', 'action_cancelled_stale', 'action_executed',
    'transition_verified', 'transition_failed', 'recovery_started',
    'checkpoint_committed', 'component_restarted', 'permission_requested', 'safe_stop',
    # Reflex lane
    'recording_started', 'recording_stopped', 'skill_compiled', 'state_matched',
    'state_blocked', 'repair_plan', 'skill_patched',
}

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
_lock = threading.Lock()


def _path():
    return os.path.join(DIR, time.strftime('%Y-%m-%d') + '.jsonl')


def emit(event: str, **fields):
    if event not in EVENTS:
        raise ValueError(f'unknown event type: {event}')
    rec = {'ts': time.time_ns() // 1_000_000, 'event': event, **fields}
    line = json.dumps(rec, separators=(',', ':'))
    with _lock:
        with open(_path(), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())
    return rec


def read(day=None):
    """Yield events; a torn last line is yielded as {'torn': raw} instead of crashing."""
    p = os.path.join(DIR, (day or time.strftime('%Y-%m-%d')) + '.jsonl')
    if not os.path.exists(p):
        return
    with open(p, encoding='utf-8') as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                yield {'torn': raw[:200]}


if __name__ == '__main__':
    # Self-test: 1k writes, then read back and count.
    t0 = time.time()
    for i in range(1000):
        emit('frame_captured', frame_id=i, test=True)
    n = sum(1 for e in read() if e.get('test'))
    print(f'wrote 1000, read {n}, {time.time() - t0:.2f}s')
    assert n >= 1000
