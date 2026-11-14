"""Motor daemon — the ONLY component permitted to emit mouse and keyboard input.

Contract (from the kernel spec):
- normalized [0..1] coordinates in, physical pixels out, after DPI/monitor calibration
- rejects actions whose source frame is stale
- one pending action at a time
- move, settle, click are separate phases
- SAFE_STOP file honored before EVERY emission — the kernel has no code path that
  removes it; only Marco deletes it. estop.py writes it from a global hotkey.
- every issued input is journaled with its source frame

Input is emitted with SendInput via ctypes. MOUSEEVENTF_ABSOLUTE uses the 0..65535
virtual-desktop space, which sidesteps per-monitor DPI entirely — the same normalized
coordinate lands on the same pixel regardless of scaling. That is the DPI calibration.
"""
import ctypes, ctypes.wintypes as wt, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'journal'))
import journal

SAFE_STOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'journal', 'SAFE_STOP')

user32 = ctypes.windll.user32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor-v2: real pixels

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

# ---- SendInput plumbing ----
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('dwFlags', wt.DWORD), ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(wt.ULONG))]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(wt.ULONG))]

class _IU(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _IU)]

MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    n = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if n != len(inputs):
        raise OSError(f'SendInput sent {n}/{len(inputs)}')


def _mouse(flags, nx=0, ny=0):
    i = INPUT(type=0)
    i.u.mi = MOUSEINPUT(int(nx), int(ny), 0, flags, 0, None)
    return i


class Motor:
    def __init__(self):
        self.pending = None            # single-pending-action invariant
        self.vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        self.vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        self.vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        self.vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

    # ---- preconditions ----
    def _gate(self, action):
        if os.path.exists(SAFE_STOP):
            journal.emit('safe_stop', action_id=action['action_id'], refused=True)
            raise PermissionError('SAFE_STOP is set — no input leaves this process')
        if self.pending:
            raise RuntimeError(f'action {self.pending} still pending — one at a time')

    def _stale(self, action, latest_frame_id, max_age_frames=8):
        age = latest_frame_id - action['source_frame_id']
        if age > max_age_frames:
            journal.emit('action_cancelled_stale', action_id=action['action_id'],
                         source_frame_id=action['source_frame_id'],
                         latest_frame_id=latest_frame_id, age=age)
            return True
        return False

    # ---- transforms ----
    def to_abs(self, nx, ny):
        """Normalized [0..1] on the virtual desktop -> SendInput 0..65535 space."""
        return round(nx * 65535), round(ny * 65535)

    def to_pixels(self, nx, ny):
        return round(self.vx + nx * self.vw), round(self.vy + ny * self.vh)

    # ---- phases: move, settle, click ----
    def click(self, action, latest_frame_id, revalidate=None):
        """action: the full action object (spec shape). revalidate: callback(px, py) -> bool
        run between settle and click — the last look before touching anything."""
        self._gate(action)
        if self._stale(action, latest_frame_id):
            return {'ok': False, 'why': 'stale'}
        self.pending = action['action_id']
        try:
            region = action['target_region']            # normalized [x, y, w, h]
            nx = region[0] + region[2] / 2
            ny = region[1] + region[3] / 2
            ax, ay = self.to_abs(nx, ny)
            _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay))
            time.sleep(0.06)                            # settle: let hover states render
            if revalidate:
                px, py = self.to_pixels(nx, ny)
                if not revalidate(px, py):
                    journal.emit('action_cancelled_stale', action_id=action['action_id'],
                                 why='revalidation failed at target')
                    return {'ok': False, 'why': 'revalidation'}
            if os.path.exists(SAFE_STOP):               # re-check across the settle gap
                journal.emit('safe_stop', action_id=action['action_id'], refused=True)
                return {'ok': False, 'why': 'safe_stop'}
            _send(_mouse(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay))
            time.sleep(0.02)
            _send(_mouse(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay))
            journal.emit('action_executed', **{k: action[k] for k in
                         ('action_id', 'source_frame_id', 'intent', 'target_region',
                          'confidence', 'expected_transition', 'timeout_ms', 'risk')},
                         phys=self.to_pixels(nx, ny))
            return {'ok': True}
        finally:
            self.pending = None

    def type_text(self, action, latest_frame_id, text):
        """Unicode typing via scancode injection. BLIND TYPING IS BANNED: caller must have
        verified focus (expected_transition on the click that focused the field)."""
        self._gate(action)
        if self._stale(action, latest_frame_id):
            return {'ok': False, 'why': 'stale'}
        self.pending = action['action_id']
        try:
            for ch in text:
                for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                    i = INPUT(type=1)
                    i.u.ki = KEYBDINPUT(0, ord(ch), flags, 0, None)
                    _send(i)
                time.sleep(0.008)
            journal.emit('action_executed', action_id=action['action_id'],
                         source_frame_id=action['source_frame_id'],
                         intent=action['intent'], typed_len=len(text),
                         confidence=action.get('confidence'),
                         expected_transition=action.get('expected_transition'),
                         risk=action.get('risk'))
            return {'ok': True}
        finally:
            self.pending = None
