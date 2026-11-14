"""Reflex recorder — capture a human demonstration as an append-only trajectory.

Frames: mss ring at ~12fps, saved as PNG only when an action references them (before/after
windows), so a 2-minute demo is dozens of frames, not thousands. Input: pynput global
listeners. Stop: Ctrl+Alt+F10. All coordinates normalized to the virtual desktop —
the same space motor.py emits into.

Durability and ordering: every event reserves its slot (sequence number, monotonic ms
timestamp, action_id) at the moment it HAPPENS, under one lock. A click's record is only
complete 450ms later, when its frame_after thread has the image; the single writer thread
holds later records until that slot arrives, so the file order is always the human's
order and action ids never duplicate or run backwards. Input callbacks never wait on the
backfill — only the writer thread does. Each line is flushed (and fsynced) as it is
written, so an externally killed process leaves complete, readable JSONL behind.

On stop: stop listeners, flush the coalesced typing into one type event, join every
pending frame_after thread, drain the writer, then close the file.
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

F10_VK = 121


def _vk(key):
    """pynput Key.f10 exposes vk as .value.vk (121); a plain KeyCode has .vk."""
    vk = getattr(key, 'vk', None)
    if vk is None:
        vk = getattr(getattr(key, 'value', None), 'vk', None)
    return vk


class Recorder:
    def __init__(self, skill_dir):
        self.dir = skill_dir
        self.frames_dir = os.path.join(skill_dir, 'frames')
        os.makedirs(self.frames_dir, exist_ok=True)
        self.traj_path = os.path.join(skill_dir, 'trajectory.jsonl')
        self.action_n = 0
        self.reserved = 0                     # next sequence number to hand out
        self.write_next = 0                   # next sequence number the file expects
        self.last_ts = 0
        self._resume_from_existing()
        self.traj = open(self.traj_path, 'a', encoding='utf-8')
        self.ring = deque(maxlen=24)          # ~2s at 12fps
        self.frame_id = 0
        self.saved = {}
        self.frame_lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.text_buf = []                    # coalesce keystrokes between clicks
        # One condition guards stamping, enqueueing and the ordered write. Stamping at
        # occurrence time is what keeps action ids unique/increasing even though a click
        # line is only complete when its 450ms backfill lands.
        self.cv = threading.Condition()
        self.pending = {}                     # seq -> finished record waiting its turn
        self.open_records = {}                # seq -> click whose backfill hasn't reported
        self.accepting = True                 # new callbacks stop being recorded at close
        self.closed = False
        self.finished = False
        self._finish_lock = threading.Lock()
        self._backfills = []
        self._backfills_lock = threading.Lock()
        self.ctrl = self.alt = False
        self.writer = threading.Thread(target=self._writer_loop, daemon=True,
                                       name='trajectory-writer')
        self.writer.start()

    def _resume_from_existing(self):
        """Continue numbering after a previous run; drop a torn trailing line.

        Appending is deliberate (a crashed demo may be resumed), so action ids must not
        restart at a1 and a half-written last line must not swallow the next record.
        """
        if not os.path.exists(self.traj_path):
            return
        with open(self.traj_path, 'rb') as f:
            data = f.read()
        if not data:
            return
        if not data.endswith(b'\n'):
            cut = data.rfind(b'\n')
            keep = cut + 1 if cut >= 0 else 0
            tail = data[keep:]
            # A complete record that only lost its newline is kept (and terminated);
            # truly partial JSON is quarantined so it can't swallow the next record.
            try:
                json.loads(tail.decode('utf-8'))
                with open(self.traj_path, 'ab') as f:
                    f.write(b'\n')
                data = data + b'\n'
            except (UnicodeDecodeError, json.JSONDecodeError):
                print(f'WARNING: dropping torn trailing line ({len(tail)} bytes)')
                with open(self.traj_path, 'wb') as f:
                    f.write(data[:keep])
                data = data[:keep]
        for raw in data.splitlines():
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self.reserved += 1
            self.write_next += 1
            self.last_ts = max(self.last_ts, int(r.get('ts') or 0))
            aid = r.get('action_id') or ''
            if aid.startswith('a') and aid[1:].isdigit():
                self.action_n = max(self.action_n, int(aid[1:]))

    # ---------- frames ----------
    def capture_loop(self):
        with mss.mss() as sct:
            mon = {'left': VX, 'top': VY, 'width': VW, 'height': VH}
            while not self.stop_flag.is_set():
                t0 = time.time()
                try:
                    img = np.array(sct.grab(mon))[:, :, :3]
                except Exception:
                    # A transient grab failure must not kill the ring for the rest
                    # of the demonstration.
                    time.sleep(0.05)
                    continue
                self.frame_id += 1
                self.ring.append((self.frame_id, time.time_ns() // 1_000_000, img))
                dt = time.time() - t0
                time.sleep(max(0, 1 / 12 - dt))

    def save_frame(self, fid_img):
        """PNG ref for a ring frame; None when there is no frame or the write failed."""
        if not fid_img:
            return None
        fid, ts, img = fid_img
        with self.frame_lock:
            if fid in self.saved:
                return self.saved[fid]
            name = f'f{fid}.png'
            try:
                ok = cv2.imwrite(os.path.join(self.frames_dir, name), img)
            except Exception:
                return None
            if not ok:
                return None
            self.saved[fid] = name
            return name

    def latest(self):
        return self.ring[-1] if self.ring else None

    def frame_before(self, ts_ms):
        """Newest frame strictly older than the event — the screen the human aimed at.

        No usable frame means an honest null: the click event is still recorded, only
        without a before-image. Nothing silently disappears.
        """
        for f in reversed(self.ring):
            if f[1] <= ts_ms:
                return f
        return None

    # ---------- event plumbing ----------
    def _stamp_locked(self, rec, action=True):
        """Reserve the event's slot now. Caller holds self.cv.

        Timestamps are made strictly monotonic so sorting by ts (what compiler.py does)
        reproduces the occurrence order exactly.
        """
        ts = time.time_ns() // 1_000_000
        if ts <= self.last_ts:
            ts = self.last_ts + 1
        self.last_ts = ts
        seq = self.reserved
        self.reserved += 1
        if action:
            self.action_n += 1
            rec['action_id'] = f'a{self.action_n}'
        rec['ts'] = ts
        rec['seq'] = seq
        return seq

    def _enqueue_locked(self, seq, rec):
        """Hand a finished record to the writer. Caller holds self.cv.

        A slot that was already written (e.g. a backfill thread reporting after the stop
        path finalized its click) is ignored instead of duplicating the line.
        """
        if self.closed or seq < self.write_next:
            return False
        self.pending[seq] = rec
        self.cv.notify_all()
        return True

    def _stamp(self, rec, action=True):
        with self.cv:
            return self._stamp_locked(rec, action)

    def _enqueue(self, seq, rec):
        """Hand a finished record to the writer; returns without waiting on the file."""
        with self.cv:
            return self._enqueue_locked(seq, rec)

    def _writer_loop(self):
        # Sole owner of self.traj: lines leave in sequence order, floored by the newest
        # slot available. A click whose frame_after is still pending simply holds the
        # records that happened after it for a few hundred ms. The write itself happens
        # outside self.cv so an fsync never stalls a pynput callback (Windows may drop a
        # low-level hook whose callback overruns).
        while True:
            with self.cv:
                while not self.closed and self.write_next not in self.pending:
                    self.cv.wait(0.1)
                if self.write_next in self.pending:
                    seq = self.write_next
                    rec = self.pending.pop(seq)
                elif self.closed and self.pending:
                    # Best effort on a timed-out drain: never drop a record just because
                    # the slot before it never landed.
                    seq = min(self.pending)
                    rec = self.pending.pop(seq)
                else:
                    return                    # closed and fully drained
            self.traj.write(json.dumps(rec, ensure_ascii=False) + '\n')
            self.traj.flush()
            try:
                os.fsync(self.traj.fileno())   # survives external termination
            except OSError:
                pass
            with self.cv:
                self.write_next = seq + 1
                self.cv.notify_all()

    def _emit_now(self, rec, action=True):
        with self.cv:
            seq = self._stamp_locked(rec, action=action)
            self._enqueue_locked(seq, rec)
        return seq

    def _flush_text_locked(self):
        """Merge buffered keystrokes into one type event, placed where they happened.

        Atomic with the caller's stamping so a type event can never be sequenced
        after the click that followed it. Caller holds self.cv.
        """
        if not self.text_buf:
            return
        text = ''.join(self.text_buf)
        self.text_buf = []
        if not text:
            return
        rec = {'input': {'type': 'type', 'text': text}}
        seq = self._stamp_locked(rec)
        self._enqueue_locked(seq, rec)

    def flush_text(self):
        with self.cv:
            self._flush_text_locked()

    # ---------- input callbacks ----------
    def on_click(self, x, y, button, pressed):
        if not pressed:
            return
        rec = {
            'cursor_position_normalized': [(x - VX) / VW, (y - VY) / VH],
            'input': {'type': f'{button.name}_click'},
        }
        with self.cv:
            if not self.accepting:
                return
            self._flush_text_locked()          # typing happened before this click
            seq = self._stamp_locked(rec)
            # Resolve frame_before under the lock: anything registered as open already
            # carries its before-image, so a stop racing this callback can never
            # finalize the click with a null it could have avoided.
            rec['frame_before'] = self.save_frame(self.frame_before(rec['ts']))
            rec['frame_after'] = None
            self.open_records[seq] = rec       # backfill still owes this slot

        # frame_after: grab the newest frame ~450ms later from a timer thread
        def after(seq=seq, rec=rec):
            try:
                time.sleep(0.45)
                frame_after = self.save_frame(self.latest())
            except Exception:
                frame_after = None             # keep the click, lose only the image
            with self.cv:
                if seq not in self.open_records:
                    return                     # stop already finalized this click
                rec['frame_after'] = frame_after
                self.open_records.pop(seq, None)
                self._enqueue_locked(seq, rec)

        t = threading.Thread(target=after, daemon=True,
                             name=f'backfill-{rec["action_id"]}')
        with self._backfills_lock:
            self._backfills.append(t)
        try:
            t.start()
        except Exception:
            # Thread could not start: the click still must reach the file.
            with self.cv:
                self.open_records.pop(seq, None)
                self._enqueue_locked(seq, rec)

    def on_scroll(self, x, y, dx, dy):
        rec = {'cursor_position_normalized': [(x - VX) / VW, (y - VY) / VH],
               'input': {'type': 'scroll', 'dy': dy}}
        with self.cv:
            if not self.accepting:
                return
            self._flush_text_locked()
            seq = self._stamp_locked(rec)
            self._enqueue_locked(seq, rec)

    def on_key(self, key):
        # stop chord (pynput Key.f10 hides its vk under .value)
        if _vk(key) == F10_VK and self.ctrl and self.alt:
            self.stop_flag.set()
            return False
        with self.cv:
            if not self.accepting:
                return
            if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                self.ctrl = True
                return
            if key in (keyboard.Key.alt_l, keyboard.Key.alt_gr):
                self.alt = True
                return
            ch = getattr(key, 'char', None)
            if ch and not (self.ctrl or self.alt):
                self.text_buf.append(ch)
            elif key == keyboard.Key.space:
                self.text_buf.append(' ')
            elif key in (keyboard.Key.enter, keyboard.Key.tab,
                         keyboard.Key.backspace, keyboard.Key.esc):
                self._flush_text_locked()
                rec = {'input': {'type': 'key', 'key': key.name}}
                seq = self._stamp_locked(rec)
                self._enqueue_locked(seq, rec)

    def on_release(self, key):
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self.ctrl = False
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_gr):
            self.alt = False

    # ---------- lifecycle ----------
    def _wait_for_stop(self, stop_file):
        while not self.stop_flag.is_set():
            if os.path.exists(stop_file):
                try:
                    os.remove(stop_file)
                except OSError:
                    pass
                self.stop_flag.set()
                break
            time.sleep(0.2)

    def _join_backfills(self, timeout):
        """Wait for every pending frame_after thread to hand its click record over."""
        deadline = time.time() + timeout
        with self._backfills_lock:
            threads = list(self._backfills)
        for t in threads:
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            t.join(remaining)
        return all(not t.is_alive() for t in threads)

    def _finalize_open_records(self):
        """A click whose backfill thread never reported still gets its line.

        frame_after stays null (the honest "no usable frame" answer) rather than the
        click disappearing from the trajectory.
        """
        with self.cv:
            for seq, rec in list(self.open_records.items()):
                rec.setdefault('frame_after', None)
                self.open_records.pop(seq, None)
                self._enqueue_locked(seq, rec)
            # also release any slots the writer saw become available out of order
            self.cv.notify_all()

    def _drain(self, timeout):
        """Wait until every reserved record has actually reached the file."""
        deadline = time.time() + timeout
        with self.cv:
            while self.write_next < self.reserved:
                remaining = deadline - time.time()
                if remaining <= 0 or self.closed:
                    return False
                self.cv.wait(remaining)
            return True

    def finish(self, ml=None, kl=None, timeout=3.0):
        """Stop listeners, flush trailing typing, join backfills, drain, close."""
        with self._finish_lock:
            if self.finished:
                return
            self.finished = True
        self.stop_flag.set()
        # 1. no new input can arrive while we are closing: stop AND join the listeners.
        #    Only after they are joined is it safe to close the intake, because a
        #    callback that was already running when stop() returned must still be
        #    recorded.
        for listener in (kl, ml):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        for listener in (kl, ml):
            if listener is not None:
                try:
                    listener.join(timeout=1.0)
                except Exception:
                    pass
        with self.cv:
            self.accepting = False
        # 2. keystrokes typed just before the stop become one type event
        self.flush_text()
        # 3. every click's frame_after thread lands, and every reserved line is on disk.
        #    (They must hand their records over BEFORE closed is set, or _enqueue drops
        #    them — the exact tail loss this redesign exists to prevent.)
        joined = self._join_backfills(timeout)
        self._finalize_open_records()         # a hung/stuck backfill still keeps its click
        if not joined:
            print('WARNING: a frame_after backfill did not finish before stop timeout')
        self.flush_text()                     # last chance for a straggling keystroke
        drained = self._drain(timeout)
        with self.cv:
            self.closed = True
            self.cv.notify_all()
        self.writer.join(timeout)
        if self.writer.is_alive():
            print('WARNING: trajectory writer still alive at close')
        try:
            self.traj.flush()
            os.fsync(self.traj.fileno())
        except (OSError, ValueError):
            pass
        try:
            self.traj.close()
        except (OSError, ValueError):
            pass
        if not drained:
            print('WARNING: trajectory closed before all pending records were drained')
        print(f'stopped — {self.action_n} actions, {len(self.saved)} frames saved')

    def run(self):
        cap = threading.Thread(target=self.capture_loop, daemon=True, name='frame-ring')
        cap.start()
        time.sleep(0.4)                        # ring needs a first frame before any click
        self._emit_now({'meta': True, 'screen': [VW, VH], 'origin': [VX, VY],
                        'dpi_note': 'coords normalized to virtual desktop'}, action=False)
        print('RECORDING — perform the workflow. Ctrl+Alt+F10 to stop.')
        ml = mouse.Listener(on_click=self.on_click, on_scroll=self.on_scroll)
        kl = keyboard.Listener(on_press=self.on_key, on_release=self.on_release)
        ml.start(); kl.start()
        # orchestratable stop: a flow script drops STOP_RECORDING next to the trajectory
        try:
            self._wait_for_stop(os.path.join(self.dir, 'STOP_RECORDING'))
        finally:
            self.finish(ml, kl)


if __name__ == '__main__':
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'skills', 'scratch')
    os.makedirs(d, exist_ok=True)
    Recorder(d).run()
