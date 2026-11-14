"""Real-tabs benchmark: REFLEX against a real Chrome tab (throwaway profile).

Why a local page in a real browser: the page must be able to CONFESS what actually
happened. On submit it writes a JSON of {field: value} into document.title — the window
title is the ground truth, read from outside the browser. A misroute (text in the wrong
field) is physically detectable, not inferred from the runtime's own confidence.

Per rep: demo on a fresh Chrome at position A with field order 1-2-3 -> compile ->
relaunch at a RANDOM position with a RANDOM field order -> replay from crops only ->
score from the title. A foreground guard aborts the rep as CONTENTION the moment any
window outside the rep's own process tree takes focus (the 2026-08-01 lesson: on a
shared desktop, contention must become a labeled abort, not a corrupted result).
"""
import ctypes, json, os, random, shutil, subprocess, sys, tempfile, threading, time, urllib.parse

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, '..', 'kernel'))
sys.path.insert(0, os.path.join(DIR, '..', 'journal'))

CHROME_CANDIDATES = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
]
CHROME = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
WORK = os.path.join(tempfile.gettempdir(), 'reflex_real')
PAGE = os.path.join(WORK, 'form.html')
SKILL = os.path.join(DIR, '..', 'skills', 'real_chrome_form')

WIN_W, WIN_H = 1000, 700
VALUES = {'Name': 'July Report', 'City': 'Marco', 'Code': 'July'}
FIELDS = ['Name', 'City', 'Code']

user32 = ctypes.windll.user32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

HTML = """<!doctype html><meta charset="utf-8"><title>REFLEX FORM</title>
<body style="margin:40px 40px 40px 240px;font:16px Consolas,monospace;background:#f4f4f8">
<!-- 240px left gutter: REFLEX strips reach 220px left of the demo cursor; with a
     40px margin they picked up desktop wallpaper that moved with every relaunch
     (measured: strip conf 0.86 on starry rows vs 0.99 on dark ones, rep 24). A wide
     uniform gutter keeps every crop inside window content. -->
<h2 style="margin-bottom:24px">Monthly Report Intake</h2>
<div id="rows"></div>
<button id="go" style="margin-top:26px;padding:12px 46px;font:bold 16px Consolas;
background:#2563eb;color:#fff;border:0;border-radius:4px">SUBMIT REPORT</button>
<script>
const q = new URLSearchParams(location.search);
const order = (q.get('order') || '123').split('').map(Number);
const defs = {1:'Name', 2:'City', 3:'Code'};
const rows = document.getElementById('rows');
for (const i of order) {
  const n = defs[i];
  rows.insertAdjacentHTML('beforeend',
    `<div style="height:70px"><label style="display:inline-block;width:120px;
     font-weight:bold">${n}</label><input id="f${n}" style="width:320px;padding:4px 8px;
     font:16px Consolas;border:2px solid #445;border-radius:3px"></div>`);
}
document.getElementById('go').onclick = () => {
  const out = {};
  for (const n of Object.values(defs)) out[n] = document.getElementById('f'+n).value;
  document.title = 'REFLEX-RESULT:' + JSON.stringify(out);
};
</script>"""


def ensure_page():
    os.makedirs(WORK, exist_ok=True)
    with open(PAGE, 'w', encoding='utf-8') as f:
        f.write(HTML)


def launch_chrome(x, y, order):
    profile = os.path.join(WORK, 'profile')
    url = 'file:///' + urllib.parse.quote(PAGE.replace('\\', '/')) + f'?order={order}'
    p = subprocess.Popen([CHROME, f'--user-data-dir={profile}', '--no-first-run',
                          '--disable-session-crashed-bubble', '--disable-infobars',
                          '--new-window', f'--window-position={x},{y}',
                          f'--window-size={WIN_W},{WIN_H}', url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3.5)
    return p


def kill_chrome():
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                    "Where-Object { $_.CommandLine -match 'reflex_real' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   capture_output=True, timeout=60)
    time.sleep(1.0)


def form_hwnd():
    """The REFLEX FORM Chrome window's handle, or None."""
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if 'REFLEX FORM' in buf.value or 'REFLEX-RESULT:' in buf.value:
                hits.append(hwnd)
        return True

    user32.EnumWindows(cb, None)
    return hits[0] if hits else None


def force_top(timeout_s=15):
    """Make the form window topmost AND verified-foreground. Returns hwnd or None.
    Without this the 04:28 smoke typed into Marco's real maximized Chrome — the form
    had opened BEHIND it and stray blue pixels on his own page passed calibration.
    No click is allowed until the foreground window IS the form."""
    HWND_TOPMOST, SWP_NOMOVE_NOSIZE_SHOW = -1, 0x0001 | 0x0002 | 0x0040
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        h = form_hwnd()
        if h:
            user32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE_NOSIZE_SHOW)
            user32.SetForegroundWindow(h)
            time.sleep(0.4)
            if user32.GetForegroundWindow() == h:
                return h
        time.sleep(0.5)
    return None


def title_of_result():
    """Find the reflex chrome window and return the REFLEX-RESULT title, if any."""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if 'REFLEX-RESULT:' in buf.value or 'REFLEX FORM' in buf.value:
                found.append(buf.value)
        return True

    user32.EnumWindows(cb, None)
    for t in found:
        if 'REFLEX-RESULT:' in t:
            return t.split('REFLEX-RESULT:', 1)[1].split(' - ')[0]
    return None


ROW_H = 70
SUBMIT_MARGIN = 26


def calibrate(save_fail_to=None, timeout_s=20):
    """No hardcoded Chrome-UI height: find the SUBMIT button's #2563eb pixels on the
    live screen and derive every click point from its measured position. Immune to
    Chrome version/UI changes; a failed calibration returns None and the rep is
    labeled DEMO-INVALID (a harness miss, never blamed on REFLEX).
    Retries because a fresh profile's first paint can lag; on final failure saves the
    grab so the miss is diagnosable (2026-08-01 smoke failed blind — never again)."""
    import numpy as np
    import cv2
    import matcher
    crumb_path = os.path.join(DIR, 'bench100', 'calibrate_log.txt')
    os.makedirs(os.path.dirname(crumb_path), exist_ok=True)

    def crumb(m):
        with open(crumb_path, 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%H:%M:%S")} {m}\n')

    try:
        t0 = time.time()
        img = None
        while time.time() - t0 < timeout_s:
            img = matcher.grab().astype(int)
            mask = (abs(img[:, :, 0] - 235) + abs(img[:, :, 1] - 99)
                    + abs(img[:, :, 2] - 37)) < 60
            # Largest CONNECTED blob only: Chrome's "Restore pages?" bubble has a
            # Google-blue button (#1a73e8, distance 30 from ours) — a global min/max
            # over BOTH blobs put the submit centroid in empty space and the field
            # grid in the tab bar (night 3, 04:42). The button must also LOOK like
            # the button: wide, short, filled.
            n, labels, stats, cents = cv2.connectedComponentsWithStats(
                mask.astype('uint8'), connectivity=8)
            best = None
            for i in range(1, n):
                x, y, w, h, area = stats[i]
                if area >= 4000 and 150 <= w <= 500 and 25 <= h <= 90 \
                        and area >= 0.7 * w * h:
                    if best is None or area > best[4]:
                        best = (x, y, w, h, area)
            crumb(f'retry: blue_px={int(mask.sum())} blobs={n - 1} '
                  f'button={"yes" if best else "no"}')
            if best is not None:
                x, y, w, h, _ = (int(v) for v in best)
                row0 = y - SUBMIT_MARGIN - 3 * ROW_H
                left = x
                # Click the LEFT end of the input, not its center: the context crop
                # reaches 220px left of the cursor, and from the input center the
                # label starts 280px away — outside every crop, so identical inputs
                # lost their only distinguishing pixels and replays routed by
                # position (measured MISROUTE, 05:40). From the left end the label
                # sits 140px in. Also where humans actually click.
                return {
                    'field': lambda row: (left + 120 + 30, row0 + row * ROW_H + 22),
                    'submit': (x + w // 2, y + h // 2),
                }
            time.sleep(1.0)
        if save_fail_to is not None and img is not None:
            ok = cv2.imwrite(save_fail_to, img.astype('uint8'))
            crumb(f'timeout - saved {save_fail_to}: {ok}')
        return None
    except Exception as e:
        crumb(f'EXCEPTION: {e!r}')
        return None


def warm_profile():
    """First launch of a brand-new --user-data-dir does one-time setup that can eat the
    demo's settle time. Pay that cost once, before any rep."""
    p = subprocess.Popen([CHROME, f'--user-data-dir={os.path.join(WORK, "profile")}',
                          '--no-first-run', 'about:blank'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(8)
    kill_chrome()


class ForegroundGuard(threading.Thread):
    """Abort flag flips the moment a window outside `allowed_pids` takes foreground."""

    def __init__(self, allowed_pids):
        super().__init__(daemon=True)
        self.allowed = set(allowed_pids)
        self.violated = False
        self.stop = False

    def run(self):
        while not self.stop:
            hwnd = user32.GetForegroundWindow()
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and pid.value not in self.allowed:
                self.violated = True
            time.sleep(0.15)

    def chrome_pids(self):
        r = subprocess.run(['powershell', '-NoProfile', '-Command',
                            "(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                            "Where-Object { $_.CommandLine -match 'reflex_real' }).ProcessId"],
                           capture_output=True, text=True, timeout=60)
        for line in r.stdout.split():
            if line.strip().isdigit():
                self.allowed.add(int(line.strip()))


def demo(motor_mod):
    """Scripted demonstration at a fixed position, canonical order 123."""
    import motor as _m
    m = _m.Motor()
    launch_chrome(180, 120, '123')
    if force_top() is None:
        return None                       # form never verified foreground: zero clicks fired
    cal = calibrate(save_fail_to=os.path.join(DIR, 'bench100', 'calibrate_fail.png'))
    if cal is None:
        return None
    VW, VH = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    VX, VY = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)

    def click(x, y):
        a = {'action_id': 'demo', 'source_frame_id': 0, 'timestamp': 0, 'intent': 'demo',
             'target_region': [(x - VX) / VW, (y - VY) / VH, 10 / VW, 10 / VH],
             'confidence': 1.0, 'expected_transition': 'demo', 'timeout_ms': 1000,
             'risk': 'none'}
        r = m.click(a, latest_frame_id=0)
        assert r['ok'], r
        time.sleep(0.9)

    def typ(text):
        a = {'action_id': 'demo', 'source_frame_id': 0, 'intent': 'demo',
             'confidence': 1.0, 'expected_transition': 'demo', 'risk': 'none'}
        assert m.type_text(a, 0, text)['ok']
        time.sleep(0.6)

    def crumb(m):
        with open(os.path.join(DIR, 'bench100', 'calibrate_log.txt'), 'a',
                  encoding='utf-8') as f:
            f.write(f'{time.strftime("%H:%M:%S")} demo: {m}\n')

    for row, name in enumerate(FIELDS):
        crumb(f'click field {name} at {cal["field"](row)}')
        click(*cal['field'](row))
        typ(VALUES[name])
    crumb(f'click submit at {cal["submit"]}')
    click(*cal['submit'])
    got = None
    for _ in range(10):                    # title flips synchronously, but be generous
        time.sleep(0.5)
        got = title_of_result()
        if got is not None:
            break
    if got is None:
        import cv2
        import matcher
        cv2.imwrite(os.path.join(DIR, 'bench100', 'post_submit.png'), matcher.grab())
        crumb('no result title - post_submit.png saved')
    return got  # demo's own ground truth; None means the scripted demo itself missed


def one_rep(rep):
    """Returns a dict verdict for one demo->compile->randomized-replay cycle.
    kill_chrome() on EVERY exit path: the 2026-08-01 smoke leaked its window through a
    DEMO-INVALID early return and the leftover covered the desktop for 99 mock runs."""
    try:
        return _one_rep_inner(rep)
    finally:
        kill_chrome()


def _one_rep_inner(rep):
    import compiler, runtime
    if os.path.exists(SKILL):
        shutil.rmtree(SKILL)
    os.makedirs(SKILL, exist_ok=True)
    kill_chrome()

    rec = subprocess.Popen([sys.executable, os.path.join(DIR, 'recorder.py'), SKILL],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        demo_truth = demo(None)
    finally:
        open(os.path.join(SKILL, 'STOP_RECORDING'), 'w').close()
        try:
            rec.wait(timeout=15)
        except subprocess.TimeoutExpired:
            rec.kill()
    kill_chrome()
    if demo_truth is None:
        return {'rep': rep, 'outcome': 'DEMO-INVALID',
                'note': 'scripted demo missed - coordinates, not REFLEX'}
    expected = json.loads(demo_truth)
    if expected != VALUES:
        return {'rep': rep, 'outcome': 'DEMO-INVALID', 'note': f'demo typed {expected}'}

    compiler.compile_skill(SKILL, 'real_chrome_form')

    order = ''.join(random.sample('123', 3))
    wx, wy = random.randrange(60, 800), random.randrange(40, 320)
    launch_chrome(wx, wy, order)
    if force_top() is None:
        return {'rep': rep, 'outcome': 'CONTENTION',
                'note': 'replay window never verified foreground - rep voided'}
    guard = ForegroundGuard({os.getpid()})
    guard.chrome_pids()
    guard.start()
    try:
        res = runtime.Run(SKILL).run()
    finally:
        guard.stop = True
    time.sleep(1.0)
    got = title_of_result()

    if guard.violated:
        return {'rep': rep, 'outcome': 'CONTENTION', 'order': order,
                'note': 'foreground stolen mid-replay — desktop not ours, rep voided'}
    if got is None:
        return {'rep': rep, 'outcome': 'BLOCKED', 'order': order,
                'note': (res.get('why') or 'no submit')[:100]}
    fields = json.loads(got)
    if fields == VALUES:
        return {'rep': rep, 'outcome': 'PERFECT', 'order': order,
                'seconds': res.get('seconds')}
    if any(fields.values()):
        return {'rep': rep, 'outcome': 'MISROUTE', 'order': order, 'fields': fields}
    return {'rep': rep, 'outcome': 'BLOCKED', 'order': order, 'fields': fields}


def main(n=30, out_path=None):
    assert CHROME, 'chrome.exe not found'
    ensure_page()
    warm_profile()
    out_path = out_path or os.path.join(DIR, 'bench100', 'real_results.jsonl')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    results = []
    for rep in range(1, n + 1):
        try:
            r = one_rep(rep)
        except Exception as e:
            r = {'rep': rep, 'outcome': 'CRASH', 'note': repr(e)[:140]}
            kill_chrome()
        results.append(r)
        with open(out_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(r) + '\n')
    return results


if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    rs = main(n)
    from collections import Counter
    print(Counter(r['outcome'] for r in rs))
