"""Acceptance tests for the recorder's stop path and trajectory durability.

No real desktop input is generated: the mss capture is swapped for a synthetic frame
source and the pynput callbacks are invoked directly, which is the same code path the
global hooks call. Covers:

  * every pending frame_after backfill lands before trajectory.jsonl is closed
  * typing buffered at stop time becomes one type event
  * each line is flushed, so an externally killed process leaves readable JSONL
  * a click with no usable ring frame keeps its event with frame_before=null
  * a backfill with no frame keeps its event with frame_after=null
  * action_id is unique, contiguous and in occurrence order under concurrency
  * reflex/compiler.py turns the trajectory into the expected number of states

Run: python tests/test_recording_flow.py
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'reflex'))
sys.path.insert(0, ROOT)          # for reflex.compiler / reflex.recorder


def _load():
    import importlib
    if 'recorder' in sys.modules:
        return importlib.reload(sys.modules['recorder'])
    import recorder
    return recorder


class FakeScreen:
    """Deterministic stand-in for mss: a gradient with a moving marker per grab.

    `state['fail']` is live so a test can break the capture and later heal it without
    swapping the object the capture loop already holds.
    """

    def __init__(self, width, height, state=None):
        self.width, self.height = width, height
        self.state = state if state is not None else {'fail': False}
        self.n = 0

    def grab(self, mon):
        if self.state['fail']:
            raise RuntimeError('simulated capture failure')
        self.n += 1
        h = np.zeros((mon['height'], mon['width'], 4), dtype=np.uint8)
        yy = np.linspace(0, 255, mon['height'], dtype=np.uint8)[:, None]
        xx = np.linspace(0, 255, mon['width'], dtype=np.uint8)[None, :]
        h[:, :, 0] = (xx.astype(int) + self.n * 37) % 256
        h[:, :, 1] = yy
        h[:, :, 2] = ((xx.astype(int) + yy.astype(int)) // 2) % 256
        h[:, :, 3] = 255
        # moving marker so before/after crops are non-blank and visibly differ
        mx = int((self.n * 53) % max(1, mon['width'] - 80))
        my = int((self.n * 29) % max(1, mon['height'] - 80))
        h[my:my + 40, mx:mx + 40, :3] = 255
        return h

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeMSSModule:
    def __init__(self, screen):
        self.screen = screen

    def mss(self):
        return self.screen


def new_recorder(tmp, width=640, height=480, capture=True, fail_capture=False):
    rec = _load()
    rec.VX, rec.VY, rec.VW, rec.VH = 0, 0, width, height
    screen = FakeScreen(width, height, {'fail': fail_capture})
    rec.mss = FakeMSSModule(screen)
    r = rec.Recorder(tmp)
    r._screen = screen
    if capture:
        t = threading.Thread(target=r.capture_loop, daemon=True)
        t.start()
        r._capture_thread = t
    return rec, r


def read_traj(tmp):
    p = os.path.join(tmp, 'trajectory.jsonl')
    with open(p, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def click(r, x, y):
    from pynput import mouse
    r.on_click(x, y, mouse.Button.left, True)


def type_text(r, text):
    from pynput import keyboard
    for ch in text:
        r.on_key(keyboard.KeyCode.from_char(ch))


def stop_like_run(r, tmp):
    """Drive the same STOP_RECORDING marker the orchestrated flow drops."""
    p = os.path.join(tmp, 'STOP_RECORDING')
    open(p, 'w').close()
    r._wait_for_stop(p)
    r.finish(None, None)


def simulate(tmp, width=640, height=480, clicks=4, typed='July Report'):
    """One scripted demonstration: N clicks with typing before the last one."""
    rec, r = new_recorder(tmp, width, height)
    try:
        time.sleep(0.35)                       # let the ring fill
        r._emit_now({'meta': True, 'screen': [width, height], 'origin': [0, 0]},
                    action=False)
        positions = [(80 + i * 70, 120 + i * 50) for i in range(clicks)]
        for i, (x, y) in enumerate(positions):
            click(r, x, y)
            time.sleep(0.55)                   # human-ish pace, backfill lands
            if i == clicks - 2:
                type_text(r, typed)
        type_text(r, ' tail')                  # typed just before the stop
        stop_like_run(r, tmp)                  # stop: flush, join, drain, close
    finally:
        try:
            r.finish(None, None)
        except Exception:
            pass
    return read_traj(tmp)


def check_ids(rows, label):
    actions = [r for r in rows if not r.get('meta')]
    ids = [r['action_id'] for r in actions]
    want = [f'a{i}' for i in range(1, len(ids) + 1)]
    assert ids == want, f'{label}: action ids not contiguous/ordered: {ids}'
    ts = [r['ts'] for r in actions]
    assert ts == sorted(ts) and len(set(ts)) == len(ts), f'{label}: ts not strictly ordered'
    # File order is occurrence order: seq must ascend by exactly one per line.
    seqs = [r['seq'] for r in rows]
    assert seqs == sorted(seqs), f'{label}: file order != occurrence order: {seqs}'
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), \
        f'{label}: seq gap (a record was dropped): {seqs}'
    return actions


def test_normal_flow():
    tmp = tempfile.mkdtemp(prefix='reflex-normal-')
    try:
        rows = simulate(tmp, clicks=4, typed='July Report')
        actions = check_ids(rows, 'normal')
        # meta + 4 clicks + 1 type (first typing) + 1 type (tail at stop)
        types = [a for a in actions if a['input']['type'] == 'type']
        clicks = [a for a in actions if a['input']['type'] == 'left_click']
        assert len(clicks) == 4, f'expected 4 click events, got {len(clicks)}'
        assert len(types) == 2, f'expected 2 type events, got {len(types)}: {types}'
        assert 'July Report' in [t['input']['text'] for t in types]
        assert ' tail' in [t['input']['text'] for t in types], \
            'typing buffered at stop was lost'
        for c in clicks:
            assert c.get('frame_before'), f'click lost frame_before: {c}'
            assert c.get('frame_after'), f'click lost frame_after (backfill not drained): {c}'
            assert os.path.exists(os.path.join(tmp, 'frames', c['frame_before']))
            assert os.path.exists(os.path.join(tmp, 'frames', c['frame_after']))
        # compile and count states: one per click, one per type
        import compiler
        skill = compiler.compile_skill(tmp, 'sim_skill')
        assert len(skill['states']) == 4 + 2, \
            f'compiled {len(skill["states"])} states, expected 6'
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_frame_failures():
    """No ring frames: click still recorded, frame refs null; compiler skips it."""
    tmp = tempfile.mkdtemp(prefix='reflex-noframe-')
    try:
        rec, r = new_recorder(tmp, capture=False)   # ring stays empty
        r._emit_now({'meta': True, 'screen': [640, 480], 'origin': [0, 0]}, action=False)
        click(r, 100, 100)                         # frame_before must be null, event kept
        time.sleep(0.55)                           # backfill runs against an empty ring
        type_text(r, 'abc')
        stop_like_run(r, tmp)
        rows = read_traj(tmp)
        actions = check_ids(rows, 'noframe')
        clicks = [a for a in actions if a['input']['type'] == 'left_click']
        assert len(clicks) == 1, f'click event dropped when no frame: {actions}'
        assert clicks[0]['frame_before'] is None
        assert clicks[0]['frame_after'] is None
        import compiler
        skill = compiler.compile_skill(tmp, 'noframe')
        assert len(skill['states']) == 1, \
            f'expected only the type state, got {len(skill["states"])}'
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_capture_grab_failure_is_survivable():
    """A grab exception must not kill the ring forever (one retry then frames return)."""
    tmp = tempfile.mkdtemp(prefix='reflex-grabfail-')
    try:
        rec, r = new_recorder(tmp, capture=True, fail_capture=True)
        time.sleep(0.3)                            # ring cannot fill: capture raises
        r._emit_now({'meta': True, 'screen': [640, 480], 'origin': [0, 0]}, action=False)
        click(r, 60, 60)                           # still an event, null frames
        time.sleep(0.2)
        r._screen.state['fail'] = False            # capture recovers
        time.sleep(0.3)                            # ~3 ring frames at 12fps
        click(r, 200, 200)
        time.sleep(0.6)
        stop_like_run(r, tmp)
        actions = check_ids(read_traj(tmp), 'grabfail')
        clicks = [a for a in actions if a['input']['type'] == 'left_click']
        assert len(clicks) == 2
        assert clicks[0]['frame_before'] is None
        assert clicks[1]['frame_before'] is not None, 'ring never recovered from grab error'
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_concurrent_ids_and_order():
    """Clicks fired from many threads: ids still contiguous and file order == occurrence."""
    tmp = tempfile.mkdtemp(prefix='reflex-concurrent-')
    try:
        rec, r = new_recorder(tmp)
        try:
            time.sleep(0.35)
            r._emit_now({'meta': True, 'screen': [640, 480], 'origin': [0, 0]},
                        action=False)
            errors = []

            def worker(base):
                try:
                    for k in range(8):
                        click(r, 50 + base * 11, 50 + k * 13)
                        if k % 3 == 0:
                            type_text(r, f'x{base}{k}')
                except Exception as e:      # pragma: no cover - failure detail
                    errors.append(e)

            threads = [threading.Thread(target=worker, args=(b,)) for b in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # do NOT sleep long: many backfills are still pending when stop arrives
            stop_like_run(r, tmp)
            assert not errors, f'callback errors: {errors}'
        finally:
            try:
                r.finish(None, None)
            except Exception:
                pass
        rows = read_traj(tmp)
        actions = check_ids(rows, 'concurrent')
        assert sum(1 for a in actions if a['input']['type'] == 'left_click') == 40, \
            'concurrent clicks lost at stop'
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_kill_leaves_readable_jsonl():
    """An externally killed recorder leaves only complete, parseable lines."""
    tmp = tempfile.mkdtemp(prefix='reflex-kill-')
    try:
        child = os.path.abspath(__file__)
        p = subprocess.Popen([sys.executable, child, '--kill-child', tmp],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # child emits clicks, waits for flushes, then blocks; we kill it hard
        time.sleep(3.0)
        p.kill()
        p.wait(timeout=10)
        rows = read_traj(tmp)
        assert rows, 'killed process wrote nothing'
        actions = [r for r in rows if not r.get('meta')]
        ids = [a['action_id'] for a in actions]
        assert ids == [f'a{i}' for i in range(1, len(ids) + 1)], \
            f'killed trajectory has bad ids: {ids}'
        for a in actions:
            if a['input']['type'] == 'left_click':
                assert 'frame_after' in a, f'partially written click line: {a}'
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_click_never_overtaken_by_later_event():
    """A click's line is written 450ms later; later events must not overtake it.

    The old recorder stamped ts at WRITE time, so a scroll 120ms after a click landed
    with an earlier ts than the click and compiler.py (which sorts by ts) replayed the
    two in the wrong order.
    """
    tmp = tempfile.mkdtemp(prefix='reflex-order-')
    try:
        rec, r = new_recorder(tmp)
        try:
            time.sleep(0.4)
            r._emit_now({'meta': True, 'screen': [640, 480], 'origin': [0, 0]},
                        action=False)
            click(r, 200, 160)
            time.sleep(0.12)
            r.on_scroll(200, 160, 0, -1)      # immediate, while backfill still pending
            time.sleep(0.6)
            stop_like_run(r, tmp)
        finally:
            try:
                r.finish(None, None)
            except Exception:
                pass
        rows = read_traj(tmp)
        actions = check_ids(rows, 'order')
        assert [a['input']['type'] for a in actions] == ['left_click', 'scroll'], \
            f'click overtaken: {[a["input"]["type"] for a in actions]}'
        sorted_by_ts = sorted(actions, key=lambda x: x['ts'])
        assert [a['input']['type'] for a in sorted_by_ts] == ['left_click', 'scroll'], \
            'ts order disagrees with occurrence order (compiler would reorder)'
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stop_immediately_after_click():
    """The original bug: stop arrives during the 450ms backfill window.

    The click must still be in the file, with its frame_after, after the type event
    typed right before the stop.
    """
    tmp = tempfile.mkdtemp(prefix='reflex-race-')
    try:
        rec, r = new_recorder(tmp)
        try:
            time.sleep(0.4)
            r._emit_now({'meta': True, 'screen': [640, 480], 'origin': [0, 0]},
                        action=False)
            click(r, 120, 140)
            type_text(r, 'late')        # typed while the backfill countdown runs
            stop_like_run(r, tmp)       # stop with ~0.45s of backfill still outstanding
        finally:
            try:
                r.finish(None, None)
            except Exception:
                pass
        actions = check_ids(read_traj(tmp), 'stop-race')
        kinds = [a['input']['type'] for a in actions]
        assert kinds == ['left_click', 'type'], f'order lost at stop: {kinds}'
        assert actions[1]['input']['text'] == 'late'
        assert actions[0]['frame_before'], 'click lost frame_before in stop race'
        assert actions[0]['frame_after'], 'click lost frame_after in stop race'
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stop_chord_and_resume():
    """Ctrl+Alt+F10 really stops (pynput hides vk under .value); resume keeps ids unique."""
    from pynput import keyboard
    tmp = tempfile.mkdtemp(prefix='reflex-chord-')
    try:
        rec, r = new_recorder(tmp, capture=False)
        r._emit_now({'meta': True, 'screen': [640, 480], 'origin': [0, 0]}, action=False)
        r._emit_now({'input': {'type': 'left_click'}})
        r.finish(None, None)
        assert [x['action_id'] for x in read_traj(tmp) if not x.get('meta')] == ['a1']

        # A second run on the same directory (resume) must continue, not restart at a1.
        rec2, r2 = new_recorder(tmp, capture=False)
        r2._emit_now({'input': {'type': 'left_click'}})
        r2.finish(None, None)
        ids = [x['action_id'] for x in read_traj(tmp) if not x.get('meta')]
        assert ids == ['a1', 'a2'], f'action ids not resumed across runs: {ids}'

        # A torn trailing line from a hard kill is quarantined, the rest survives.
        with open(os.path.join(tmp, 'trajectory.jsonl'), 'a', encoding='utf-8') as f:
            f.write('{"action_id": "a3", "input": {"typ')
        rec3, r3 = new_recorder(tmp, capture=False)
        try:
            rows = read_traj(tmp)
            assert len(rows) == 3, f'torn line not removed: {len(rows)} rows'
            # stop chord through the real handler
            r3.ctrl = r3.alt = True
            handled = r3.on_key(keyboard.Key.f10)
            assert handled is False, 'stop chord did not stop the keyboard listener'
            assert r3.stop_flag.is_set(), 'Ctrl+Alt+F10 did not raise the stop flag'
            # F10 alone is NOT the stop chord
            r3.stop_flag.clear(); r3.ctrl = r3.alt = False
            r3.on_key(keyboard.Key.f10)
            assert not r3.stop_flag.is_set(), 'bare F10 stopped the recording'
        finally:
            r3.finish(None, None)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _kill_child(tmp):
    rec, r = new_recorder(tmp)
    time.sleep(0.5)
    r._emit_now({'meta': True, 'screen': [640, 480], 'origin': [0, 0]}, action=False)
    for i in range(3):
        click(r, 100 + i * 40, 100 + i * 30)
        time.sleep(0.6)                        # backfill definitely landed & flushed
        type_text(r, f'text{i}')
        r.flush_text()
    # now hold the process open so the parent can SIGKILL it mid-life
    time.sleep(120)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--kill-child':
        _kill_child(sys.argv[2])
        return
    if sys.platform != 'win32':
        print('SKIP: recorder binds Windows virtual-desktop metrics and global hooks')
        return
    tests = [test_normal_flow, test_frame_failures,
             test_capture_grab_failure_is_survivable,
             test_concurrent_ids_and_order, test_kill_leaves_readable_jsonl,
             test_click_never_overtaken_by_later_event,
             test_stop_immediately_after_click, test_stop_chord_and_resume]
    failed = []
    for t in tests:
        try:
            t()
            print(f'PASS  {t.__name__}')
        except Exception as e:
            failed.append((t.__name__, e))
            print(f'FAIL  {t.__name__}: {e}')
    print(f'\n{len(tests) - len(failed)}/{len(tests)} passed')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
