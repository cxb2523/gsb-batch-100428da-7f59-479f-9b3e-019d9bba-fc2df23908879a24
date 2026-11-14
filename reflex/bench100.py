"""100-run unattended REFLEX benchmark.

Waits for the desktop to go idle (the job-machine collision of 2026-08-01 02:46 proved
a shared desktop poisons runs), pauses the desktop-touching scheduled tasks, runs
flow.py N times, scores every run from the mock's own ground truth, writes a summary
table, re-enables the tasks in a finally. Progress and verdicts land in bench100/.
"""
import ctypes, json, os, re, subprocess, time

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DIR, 'bench100')
PY = r'C:\Python313\python.exe'
N = 100
IDLE_NEEDED_S = 90
MAX_WAIT_S = 6 * 3600
TASKS = ['ClaudeIgLocalTick', 'ClaudeApplyQueue', 'ClaudeHuntTick']
EXPECT_FIELDS = {'Title': 'July Report', 'Owner': 'Marco', 'Month': 'July'}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]


def idle_seconds():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0


def progress(msg):
    with open(os.path.join(OUT, 'progress.txt'), 'a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%H:%M:%S")} {msg}\n')


def schtask(name, flag):
    subprocess.run(['schtasks', '/change', '/tn', name, flag],
                   capture_output=True, timeout=30)


def kill_stray_python():
    # never kill ourselves: filter by command line containing flow/mock/recorder
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
          "Where-Object { $_.CommandLine -match 'flow.py|mock_app|recorder.py' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                   capture_output=True, timeout=60)


def score(log_text):
    r = {'done': 'workflow DONE      : True' in log_text,
         'row': 'correct row        : True' in log_text}
    m = re.search(r'fields typed       : (\{.*\})', log_text)
    fields = None
    if m:
        try:
            fields = eval(m.group(1), {'__builtins__': {}})  # trusted local log
        except Exception:
            fields = None
    r['fields_ok'] = fields == EXPECT_FIELDS
    r['fields'] = fields
    b = re.search(r'BLOCKED at (\S+): (.*)', log_text)
    if b:
        r['blocked'] = f'{b.group(1)}: {b.group(2)[:90]}'
    # misroute = some field got text it should not have (poison outcome)
    r['misroute'] = bool(fields) and not r['fields_ok'] and any(fields.values())
    r['perfect'] = r['done'] and r['row'] and r['fields_ok']
    sm = re.search(r'"seconds": ([0-9.]+)', log_text)
    r['seconds'] = float(sm.group(1)) if sm else None
    return r


def main():
    os.makedirs(OUT, exist_ok=True)
    if os.environ.get('BENCH_SKIP_IDLE') != '1':
        progress(f'waiting for idle >= {IDLE_NEEDED_S}s')
        t0 = time.time()
        while idle_seconds() < IDLE_NEEDED_S:
            if time.time() - t0 > MAX_WAIT_S:
                progress('gave up waiting for idle')
                return
            time.sleep(20)
    progress('desktop idle — starting')
    for t in TASKS:
        schtask(t, '/disable')
    progress('tickers disabled')
    results = []
    try:
        for i in range(1, N + 1):
            log_path = os.path.join(OUT, f'r{i:03d}.log')
            try:
                with open(log_path, 'w', encoding='utf-8') as lf:
                    p = subprocess.run([PY, '-u', os.path.join(DIR, 'flow.py')],
                                       stdout=lf, stderr=subprocess.STDOUT,
                                       timeout=150, cwd=DIR)
                crashed = p.returncode != 0
            except subprocess.TimeoutExpired:
                crashed = True
                kill_stray_python()
            # errors='replace': the child writes cp1252 (recorder's em-dash, 0x97) and a
            # strict utf-8 read killed the whole batch on run 1 (measured 03:01, 2026-08-01)
            try:
                txt = open(log_path, encoding='utf-8', errors='replace').read() \
                    if os.path.exists(log_path) else ''
                r = {'run': i, 'crashed': crashed, **score(txt)}
            except Exception as e:
                r = {'run': i, 'crashed': True, 'perfect': False, 'misroute': False,
                     'score_error': repr(e)[:120], 'seconds': None}
            results.append(r)
            with open(os.path.join(OUT, 'results.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(r) + '\n')
            progress(f"run {i}/{N}: "
                     f"{'PERFECT' if r['perfect'] else ('MISROUTE' if r['misroute'] else ('BLOCKED' if r.get('blocked') else 'CRASH'))}")
            if crashed:
                kill_stray_python()
                time.sleep(2)
            # Circuit breaker: 8 consecutive non-perfect runs = the environment is
            # poisoned (a covering window cost 99 runs on 2026-08-01). Stop burning
            # the night; the summary will show the abort.
            recent = results[-8:]
            if len(recent) == 8 and not any(x['perfect'] for x in recent):
                progress('CIRCUIT BREAKER: 8 consecutive failures - environment '
                         'poisoned, aborting batch')
                break
    finally:
        for t in TASKS:
            schtask(t, '/enable')
        progress('tickers re-enabled')
        perfect = sum(1 for r in results if r['perfect'])
        misroute = sum(1 for r in results if r['misroute'])
        blocked = sum(1 for r in results if r.get('blocked') and not r['misroute'])
        crash = sum(1 for r in results if r['crashed'] and not r.get('blocked') and not r['misroute'])
        secs = [r['seconds'] for r in results if r['perfect'] and r['seconds']]
        with open(os.path.join(OUT, 'summary.md'), 'w', encoding='utf-8') as f:
            f.write(f"""# REFLEX 100-run benchmark — {time.strftime('%Y-%m-%d')}

| outcome | count |
|---|---|
| PERFECT (all fields exact, correct shuffled row, DONE) | {perfect} |
| MISROUTE (typed into a wrong field — the poison outcome) | {misroute} |
| BLOCKED (honest refusal, nothing wrong typed) | {blocked} |
| CRASH/other | {crash} |

runs: {len(results)} · replay seconds (perfect runs): min {min(secs) if secs else '-'} / median {sorted(secs)[len(secs)//2] if secs else '-'} / max {max(secs) if secs else '-'} · LLM calls per replay: 0
Every run: fresh demo -> compile -> relaunch at random window position, shuffled rows, random accent color -> replay from crops only -> scored by the mock's own ground-truth log.
""")
        open(os.path.join(OUT, 'DONE'), 'w').close()
        progress(f'DONE perfect={perfect} misroute={misroute} blocked={blocked} crash={crash}')


if __name__ == '__main__':
    main()
