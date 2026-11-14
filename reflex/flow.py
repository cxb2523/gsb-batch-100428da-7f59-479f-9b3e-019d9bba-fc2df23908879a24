"""The defining loop, end to end:
demonstrate (motor plays the human, mock layout plays their eyes) -> recorder captures
pixels+input -> compile -> relaunch mock at a NEW random position with shuffled rows ->
runtime replays from CROPS ONLY -> score from the mock's own ground-truth log.
"""
import ctypes, json, os, shutil, subprocess, sys, time, uuid

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, '..', 'kernel'))
PY = sys.executable
SKILL = os.path.join(DIR, '..', 'skills', 'report_demo')
LAYOUT = os.path.join(DIR, 'mock_layout.json')
HITS = os.path.join(DIR, 'mock_hits.jsonl')

import motor as motor_mod

user32 = ctypes.windll.user32
SM = user32.GetSystemMetrics
VX, VY, VW, VH = SM(76), SM(77), SM(78), SM(79)


def widgets():
    try:
        with open(LAYOUT, encoding='utf-8') as f:
            return json.load(f)['widgets']
    except Exception:
        return {}


def wait_widget(key, timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        w = widgets().get(key)
        if w:
            return w
        time.sleep(0.15)
    raise TimeoutError(f'widget {key} never appeared')


def demo_click(m, key):
    x, y, w, h = wait_widget(key)
    time.sleep(0.35)                       # human-ish pause; recorder needs frame_before
    region = [(x - VX) / VW, (y - VY) / VH, w / VW, h / VH]
    action = {'action_id': str(uuid.uuid4())[:8], 'source_frame_id': 0, 'timestamp': 0,
              'intent': f'demo {key}', 'target_region': region, 'confidence': 1.0,
              'expected_transition': 'demo', 'timeout_ms': 1000, 'risk': 'none'}
    r = m.click(action, latest_frame_id=0)
    assert r['ok'], f'demo click failed at {key}: {r}'
    time.sleep(0.7)                        # let frame_after land in the recorder


def demo_type(m, text):
    action = {'action_id': str(uuid.uuid4())[:8], 'source_frame_id': 0,
              'intent': 'demo type', 'confidence': 1.0,
              'expected_transition': 'demo', 'risk': 'none'}
    r = m.type_text(action, 0, text)
    assert r['ok']
    time.sleep(0.4)


def hits_since(ts):
    out = []
    if os.path.exists(HITS):
        with open(HITS, encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r['ts'] >= ts:
                        out.append(r)
                except json.JSONDecodeError:
                    pass
    return out


def main():
    for p in (LAYOUT, HITS):
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(SKILL):
        shutil.rmtree(SKILL)
    os.makedirs(SKILL, exist_ok=True)

    print('=== 1. DEMONSTRATION (recorder listening, motor plays the human) ===')
    mock = subprocess.Popen([PY, os.path.join(DIR, 'mock_app.py')])
    rec = subprocess.Popen([PY, os.path.join(DIR, 'recorder.py'), SKILL])
    try:
        time.sleep(2.5)
        m = motor_mod.Motor()
        t_demo = time.time()
        demo_click(m, 'btn:Reports')
        demo_click(m, 'menu:New Monthly Report')
        demo_click(m, 'field:Title');  demo_type(m, 'July Report')
        demo_click(m, 'field:Owner');  demo_type(m, 'Marco')
        demo_click(m, 'field:Month');  demo_type(m, 'July')
        demo_click(m, 'btn:NEXT')
        demo_click(m, 'row:Initech')
        demo_click(m, 'btn:CONFIRM')
        time.sleep(3.5)                    # spinner -> DONE
        demo_secs = time.time() - t_demo
    finally:
        # A crashed demo must never orphan the recorder: it holds trajectory.jsonl
        # open and every later run dies on WinError 32 (measured, 2026-08-01).
        open(os.path.join(SKILL, 'STOP_RECORDING'), 'w').close()
        try:
            rec.wait(timeout=15)
        except subprocess.TimeoutExpired:
            rec.kill()
        mock.terminate()
    time.sleep(1)

    print('=== 2. COMPILE ===')
    import compiler
    skill = compiler.compile_skill(SKILL, 'report_demo')

    print('=== 3. REPLAY against a RELAUNCHED mock (new position, shuffled rows) ===')
    t_mark = time.time_ns() // 1_000_000
    mock2 = subprocess.Popen([PY, os.path.join(DIR, 'mock_app.py')])
    try:
        time.sleep(2.5)
        import runtime
        res = runtime.Run(SKILL).run()
        time.sleep(3.5)                    # spinner on the replay side
        got = hits_since(t_mark)
    finally:
        mock2.terminate()

    steps = [h.get('step') for h in got]
    done = [h for h in got if h.get('step') == 'done']
    row = [h for h in got if h.get('step') == 'row_clicked']
    fields = {h['name']: h.get('value') for h in got if h.get('step') == 'field'}
    print('\n=== VERDICT (from the mock\'s own ground truth) ===')
    print(f'demo took          : {demo_secs:.1f}s')
    print(f'replay result      : {json.dumps(res)}')
    print(f'steps ground truth : {steps}')
    print(f'fields typed       : {fields}')
    print(f'correct row        : {row and row[-1].get("correct")}')
    print(f'workflow DONE      : {bool(done)}')
    print(f'LLM calls          : {res.get("llm_calls")}')


if __name__ == '__main__':
    main()
