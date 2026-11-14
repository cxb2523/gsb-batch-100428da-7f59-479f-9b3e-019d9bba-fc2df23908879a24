"""End-to-end check of the real recording process, without a human at the keyboard.

Starts reflex/recorder.py as its own process (real mss capture, real file handle, real
stop-file polling loop), drives the global-hook callbacks in that process via a small
injected driver, drops STOP_RECORDING, and verifies the trajectory the compiler reads.
"""
import json, os, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = os.path.join(ROOT, 'reflex', 'recorder.py')

CHILD = r'''
import os, sys, time, threading
sys.path.insert(0, r"%(reflex)s")
import recorder
from pynput import mouse, keyboard

d = sys.argv[1]
r = recorder.Recorder(d)
threading.Thread(target=r.capture_loop, daemon=True).start()
time.sleep(0.5)
r._emit_now({'meta': True, 'screen': [recorder.VW, recorder.VH],
             'origin': [recorder.VX, recorder.VY]}, action=False)

def click_at(fx, fy):
    r.on_click(int(recorder.VX + fx * recorder.VW),
               int(recorder.VY + fy * recorder.VH), mouse.Button.left, True)

def typ(s):
    for ch in s:
        r.on_key(keyboard.KeyCode.from_char(ch))

click_at(0.30, 0.25); time.sleep(0.55)
typ('July Report'); time.sleep(0.10)
click_at(0.55, 0.40); time.sleep(0.55)
typ('Marco'); time.sleep(0.10)
click_at(0.70, 0.62); time.sleep(0.55)
# typing that must survive the stop: no pause long enough to flush it
typ('tail')
r._wait_for_stop(os.path.join(d, 'STOP_RECORDING'))
r.finish(None, None)
'''

def main():
    tmp = tempfile.mkdtemp(prefix='reflex-e2e-')
    try:
        child = CHILD % {'reflex': os.path.join(ROOT, 'reflex')}
        p = subprocess.Popen([sys.executable, '-c', child, tmp],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
        # wait for the child to reach its stop poll (it writes the trajectory as it goes)
        deadline = time.time() + 20
        while time.time() < deadline:
            if os.path.exists(os.path.join(tmp, 'trajectory.jsonl')):
                n = sum(1 for _ in open(os.path.join(tmp, 'trajectory.jsonl'),
                                        encoding='utf-8'))
                if n >= 4:        # meta + a click + type + a click
                    break
            time.sleep(0.2)
        open(os.path.join(tmp, 'STOP_RECORDING'), 'w').close()
        out, _ = p.communicate(timeout=20)
        print(out.strip())
        assert p.returncode == 0, f'recorder exited {p.returncode}'

        rows = [json.loads(l) for l in open(os.path.join(tmp, 'trajectory.jsonl'),
                                            encoding='utf-8') if l.strip()]
        actions = [r for r in rows if not r.get('meta')]
        ids = [a['action_id'] for a in actions]
        assert ids == [f'a{i}' for i in range(1, len(ids) + 1)], f'ids: {ids}'
        types = [a['input']['text'] for a in actions if a['input']['type'] == 'type']
        clicks = [a for a in actions if a['input']['type'] == 'left_click']
        assert len(clicks) == 3, f'expected 3 clicks, got {len(clicks)}'
        assert types == ['July Report', 'Marco', 'tail'], f'type events: {types}'
        for c in clicks:
            assert c['frame_before'], f'missing frame_before: {c}'
            assert c['frame_after'], f'missing frame_after (backfill lost): {c}'
            assert os.path.exists(os.path.join(tmp, 'frames', c['frame_before']))
            assert os.path.exists(os.path.join(tmp, 'frames', c['frame_after']))
            assert os.path.getsize(os.path.join(tmp, 'frames', c['frame_after'])) > 0

        sys.path.insert(0, os.path.join(ROOT, 'reflex'))
        import compiler
        skill = compiler.compile_skill(tmp, 'e2e')
        # 3 click states + 3 type states
        assert len(skill['states']) == 6, f'compiled {len(skill["states"])} states'
        chain = []
        sid = skill['entry_state']
        while sid:
            chain.append(sid)
            sid = skill['states'][sid]['next_state']
        assert len(chain) == 6, f'next_state chain broken: {chain}'
        order = [skill['states'][s]['action']['type'] for s in chain]
        assert order == ['left_click', 'type'] * 3, f'state order: {order}'
        print(f'E2E OK — {len(rows)} lines, {len(actions)} actions, '
              f'{len(skill["states"])} states, {len(os.listdir(tmp + "/frames"))} frames')
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
