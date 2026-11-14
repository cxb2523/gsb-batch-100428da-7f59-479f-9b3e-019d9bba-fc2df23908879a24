"""Reflex recorder — capture a human demonstration as an append-only trajectory.

Frames: mss ring at ~12fps, saved as PNG only when an action references them (before/after
windows), so a 2-minute demo is dozens of frames, not thousands. Input: pynput global
listeners. Stop: Ctrl+Alt+F10. All coordinates normalized to the virtual desktop —
the same space motor.py emits into.
"""
import ctypes, json, os, sys, threading, time
from collections import deque

import mss
import numpy as np
import cv2
from pynput import mouse, keyboard

user32 = ctypes.windll.user32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

SM = user32.GetSystemMetrics
VX, VY, VW, VH = SM(76), SM(77), SM(78), SM(79)


class Recorder:
    def __init__(self, skill_dir):
        self.dir = skill_dir
        self.frames_dir = os.path.join(skill_dir, 'frames')
        os.makedirs(self.frames_dir, exist_ok=True)
        self.traj = open(os.path.join(skill_dir, 'trajectory.jsonl'), 'a', encoding='utf-8')
        self.ring = deque(maxlen=24)          # ~2s at 12fps
        self.frame_id = 0
        self.saved = {}
        self.stop_flag = threading.Event()
        self.action_n = 0
        self.text_buf = []                    # coalesce keystrokes between clicks
        self.last_click_ts = 0

    # ---------- frames ----------
    def capture_loop(self):
        with mss.mss() as sct:
            mon = {'left': VX, 'top': VY, 'width': VW, 'height': VH}
            while not self.stop_flag.is_set():
                t0 = time.time()
                img = np.array(sct.grab(mon))[:, :, :3]
                self.frame_id += 1
                self.ring.append((self.frame_id, time.time_ns() // 1_000_000, img))
                dt = time.time() - t0
                time.sleep(max(0, 1 / 12 - dt))

    def save_frame(self, fid_img):
        fid, ts, img = fid_img
        if fid in self.saved:
            return self.saved[fid]
        name = f'f{fid}.png'
        cv2.imwrite(os.path.join(self.frames_dir, name), img)
        self.saved[fid] = name
        return name

    def latest(self):
        return self.ring[-1] if self.ring else None

    def frame_before(self, ts_ms):
        """Newest frame strictly older than the event — the screen the human aimed at."""
        for f in reversed(self.ring):
            if f[1] <= ts_ms:
                return f
        return self.ring[0] if self.ring else None

    # ---------- events ----------
    def emit(self, rec):
        rec['ts'] = time.time_ns() // 1_000_000
        self.traj.write(json.dumps(rec) + '\n')
        self.traj.flush()

    def flush_text(self):
        if not self.text_buf:
            return
        text = ''.join(self.text_buf)
        self.text_buf = []
        self.action_n += 1
        self.emit({'action_id': f'a{self.action_n}', 'input': {'type': 'type', 'text': text}})

    def on_click(self, x, y, button, pressed):
        if not pressed:
            return
        self.flush_text()
        ts = time.time_ns() // 1_000_000
        before = self.frame_before(ts)
        self.action_n += 1
        rec = {
            'action_id': f'a{self.action_n}',
            'cursor_position_normalized': [(x - VX) / VW, (y - VY) / VH],
            'input': {'type': f'{button.name}_click'},
            'frame_before': self.save_frame(before) if before else None,
        }
        # frame_after: grab the newest frame ~450ms later from a timer thread
        def after():
            time.sleep(0.45)
            f = self.latest()
            rec['frame_after'] = self.save_frame(f) if f else None
            self.emit(rec)
        threading.Thread(target=after, daemon=True).start()

    def on_scroll(self, x, y, dx, dy):
        self.flush_text()
        self.action_n += 1
        self.emit({'action_id': f'a{self.action_n}',
                   'cursor_position_normalized': [(x - VX) / VW, (y - VY) / VH],
                   'input': {'type': 'scroll', 'dy': dy}})

    def on_key(self, key):
        # stop chord
        if getattr(key, 'vk', None) == 121 and self.ctrl and self.alt:   # F10
            self.stop_flag.set()
            return False
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl = True
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_gr):
            self.alt = True
        ch = getattr(key, 'char', None)
        if ch and not (self.ctrl or self.alt):
            self.text_buf.append(ch)
        elif key == keyboard.Key.space:
            self.text_buf.append(' ')
        elif key in (keyboard.Key.enter, keyboard.Key.tab, keyboard.Key.backspace, keyboard.Key.esc):
            self.flush_text()
            self.action_n += 1
            self.emit({'action_id': f'a{self.action_n}', 'input': {'type': 'key', 'key': key.name}})

    def on_release(self, key):
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl = False
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_gr):
            self.alt = False

    ctrl = alt = False

    def run(self):
        t = threading.Thread(target=self.capture_loop, daemon=True)
        t.start()
        time.sleep(0.4)                        # ring needs a first frame before any click
        self.emit({'meta': True, 'screen': [VW, VH], 'origin': [VX, VY],
                   'dpi_note': 'coords normalized to virtual desktop'})
        print('RECORDING — perform the workflow. Ctrl+Alt+F10 to stop.')
        ml = mouse.Listener(on_click=self.on_click, on_scroll=self.on_scroll)
        kl = keyboard.Listener(on_press=self.on_key, on_release=self.on_release)
        ml.start(); kl.start()
        # orchestratable stop: a flow script drops STOP_RECORDING next to the trajectory
        stop_file = os.path.join(self.dir, 'STOP_RECORDING')
        while not self.stop_flag.is_set():
            if os.path.exists(stop_file):
                os.remove(stop_file)
                self.stop_flag.set()
            time.sleep(0.2)
        time.sleep(0.6)                        # let trailing frame_after threads land
        self.flush_text()
        ml.stop(); kl.stop()
        self.traj.close()
        print(f'stopped — {self.action_n} actions, {len(self.saved)} frames saved')


if __name__ == '__main__':
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'skills', 'scratch')
    os.makedirs(d, exist_ok=True)
    Recorder(d).run()
