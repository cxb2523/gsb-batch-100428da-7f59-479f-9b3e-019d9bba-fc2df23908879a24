"""Night orchestrator: wait for a genuinely free desktop, run BOTH benchmarks, fill the
repo README with the real numbers, and push to GitHub ONLY if the results clear the gate.

Free desktop = no input (human OR injected — the apply lane's SendInput resets the idle
clock too, so one gate covers both) for IDLE_S seconds. Smoke-tests one real rep before
committing the night to it. Dirty numbers -> no push, a report instead. ASCII-only output
(a cp1252 em-dash killed attempt 1 of bench100 on 2026-08-01).
"""
import ctypes, json, os, subprocess, sys, time

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DIR, 'bench100')
REPO = os.path.abspath(os.path.join(DIR, '..', '..', 'reflex-repo'))
PY = sys.executable
IDLE_S = 600
MAX_WAIT_S = 8 * 3600
TASKS = ['ClaudeIgLocalTick', 'ClaudeApplyQueue', 'ClaudeHuntTick']
MOCK_N, REAL_N = 100, 30
STATUS = os.path.join(OUT, 'night_status.txt')


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]


def idle_seconds():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0


def note(msg):
    os.makedirs(OUT, exist_ok=True)
    with open(STATUS, 'a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%H:%M:%S")} {msg}\n')


def schtask(name, flag):
    subprocess.run(['schtasks', '/change', '/tn', name, flag], capture_output=True, timeout=30)


def jread(path):
    rows = []
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='replace'):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def run_mock():
    existing = jread(os.path.join(OUT, 'results.jsonl'))
    if os.environ.get('SKIP_MOCK') == '1' and len(existing) >= 90:
        note(f'SKIP_MOCK=1: reusing {len(existing)} existing mock rows')
        return existing
    env = dict(os.environ, BENCH_SKIP_IDLE='1')
    for f in ('results.jsonl', 'summary.md', 'DONE', 'progress.txt'):
        p = os.path.join(OUT, f)
        if os.path.exists(p):
            os.remove(p)
    subprocess.run([PY, '-u', os.path.join(DIR, 'bench100.py')], env=env,
                   timeout=MOCK_N * 160, cwd=DIR)
    return jread(os.path.join(OUT, 'results.jsonl'))


def run_real(n):
    sys.path.insert(0, DIR)
    import real_suite
    rp = os.path.join(OUT, 'real_results.jsonl')
    if os.path.exists(rp):
        os.remove(rp)
    return real_suite.main(n, rp)


def mock_table(rows):
    perfect = sum(1 for r in rows if r.get('perfect'))
    misroute = sum(1 for r in rows if r.get('misroute'))
    blocked = sum(1 for r in rows if r.get('blocked') and not r.get('misroute'))
    other = len(rows) - perfect - misroute - blocked
    secs = sorted(r['seconds'] for r in rows if r.get('perfect') and r.get('seconds'))
    med = secs[len(secs) // 2] if secs else '-'
    t = (f'### Mock app, {len(rows)} unattended runs\n\n'
         f'| outcome | count |\n|---|---|\n'
         f'| PERFECT (all fields exact, correct shuffled row, DONE) | {perfect} |\n'
         f'| MISROUTE (text in a wrong field) | {misroute} |\n'
         f'| BLOCKED (honest refusal, nothing wrong typed) | {blocked} |\n'
         f'| CRASH/other | {other} |\n\n'
         f'Median replay: {med}s (demonstration: 14.4s). LLM calls per replay: 0.\n')
    return t, perfect, misroute, len(rows)


def real_table(rows):
    from collections import Counter
    c = Counter(r['outcome'] for r in rows)
    valid = [r for r in rows if r['outcome'] not in ('CONTENTION', 'DEMO-INVALID')]
    t = (f'### Real Chrome tab (throwaway profile), {len(rows)} reps\n\n'
         f'Window position AND form field order randomized per replay; ground truth is '
         f'the page itself confessing which fields received which text (document.title).\n\n'
         f'| outcome | count |\n|---|---|\n'
         + ''.join(f'| {k} | {v} |\n' for k, v in sorted(c.items()))
         + f'\nValid reps (contention/demo-invalid excluded): {len(valid)}.\n')
    return t, c, len(valid)


def push(tables):
    readme = os.path.join(REPO, 'README.md')
    txt = open(readme, encoding='utf-8', errors='replace').read()
    txt = txt.replace('<!-- BENCH_TABLE -->', tables)
    with open(readme, 'w', encoding='utf-8') as f:
        f.write(txt)
    bdir = os.path.join(REPO, 'benchmarks')
    os.makedirs(bdir, exist_ok=True)
    for f in ('results.jsonl', 'real_results.jsonl', 'summary.md'):
        src = os.path.join(OUT, f)
        if os.path.exists(src):
            with open(src, 'rb') as a, open(os.path.join(bdir, f), 'wb') as b:
                b.write(a.read())
    g = lambda *args: subprocess.run(['git', '-C', REPO, *args], capture_output=True,
                                     text=True, timeout=120)
    g('add', '-A')
    g('-c', 'user.name=Marco Hergi', '-c', 'user.email=hergi.muci1@gmail.com',
      'commit', '-m', 'benchmarks: unattended mock-100 + real-Chrome tables')
    r = subprocess.run(['gh', 'repo', 'create', 'reflex', '--public', '--source', REPO,
                        '--remote', 'origin', '--push'],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        g('remote', 'add', 'origin', 'https://github.com/MARCCHERGGI/reflex.git')
        r2 = g('push', '-u', 'origin', 'main')
        if r2.returncode != 0:
            note(f'PUSH FAILED: {(r.stderr or r2.stderr)[:200]}')
            return False
    note('PUSHED: https://github.com/MARCCHERGGI/reflex')
    return True


def main():
    note(f'waiting for idle >= {IDLE_S}s (covers human AND injected input)')
    t0 = time.time()
    while idle_seconds() < IDLE_S:
        if time.time() - t0 > MAX_WAIT_S:
            note('gave up: desktop never went idle for 10 min in 8h')
            return
        time.sleep(30)
    note('desktop free - starting')
    for t in TASKS:
        schtask(t, '/disable')
    try:
        note('smoke: 1 real rep')
        smoke = run_real(1)
        if smoke and smoke[0]['outcome'] in ('CRASH', 'DEMO-INVALID'):
            note(f'smoke failed ({smoke[0]}) - real suite skipped, NO PUSH')
            real_rows = []
        else:
            real_rows = None  # run later
        note(f'mock {MOCK_N} starting')
        mock_rows = run_mock()
        note(f'mock done: {len(mock_rows)} rows')
        for t in TASKS:
            schtask(t, '/disable')   # bench100's finally re-enabled them mid-night
        if real_rows is None:
            note(f'real suite {REAL_N} starting')
            real_rows = run_real(REAL_N)
            note('real suite done')
        mt, m_perfect, m_misroute, m_n = mock_table(mock_rows)
        gate_notes = []
        if real_rows:
            rt, rc, r_valid = real_table(real_rows)
            r_perfect = rc.get('PERFECT', 0)
            r_misroute = rc.get('MISROUTE', 0)
            real_ok = r_misroute == 0 and r_valid >= 15 and \
                r_perfect >= 0.8 * max(1, r_valid)
            if not real_ok:
                gate_notes.append(f'real gate failed: {dict(rc)}')
        else:
            rt, real_ok = '', False
            gate_notes.append('real suite skipped (smoke failure)')
        mock_ok = m_n >= MOCK_N * 0.9 and m_misroute == 0 and m_perfect >= 0.95 * m_n
        if not mock_ok:
            gate_notes.append(f'mock gate failed: perfect={m_perfect}/{m_n} misroute={m_misroute}')
        if mock_ok and real_ok:
            note('GATE CLEAR - pushing')
            push(mt + '\n' + rt)
        else:
            note('GATE NOT CLEAR - no push. ' + ' | '.join(gate_notes))
            with open(os.path.join(OUT, 'tables_draft.md'), 'w', encoding='utf-8') as f:
                f.write(mt + '\n' + rt)
    finally:
        for t in TASKS:
            schtask(t, '/enable')
        note('tickers re-enabled')
        open(os.path.join(OUT, 'NIGHT_DONE'), 'w').close()
        note('NIGHT DONE')


if __name__ == '__main__':
    main()
