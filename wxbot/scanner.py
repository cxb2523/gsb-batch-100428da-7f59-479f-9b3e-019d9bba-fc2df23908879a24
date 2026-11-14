# wxbot/scanner.py
"""Scanner 模块 — 窗口管理 + 截图 + 点击

所有布局/裁切/时序参数通过 config dict 配置。
"""

import os
import subprocess
import time
import logging
from typing import Optional, Tuple

import mss
import numpy as np
import psutil
import pyautogui
import win32con
import win32gui
import win32process
from PIL import Image
from pynput.mouse import Controller as Mouse, Button
from pynput.keyboard import Controller as Kb, Key

logger = logging.getLogger(__name__)


class Scanner:
    """企业微信窗口管理 + 截图 + 点击"""

    def __init__(self, config: dict = None):
        cfg = config or {}

        # --- 窗口 ---
        wc = cfg.get("wecom", {})
        self._process_names = wc.get("process_names", ["WXWork.exe", "wxwork.exe"])
        self._window_class = wc.get("window_class", "WeWorkWindow")
        self._min_title_len = wc.get("min_title_length", 2)
        self._min_w = wc.get("min_window_width", 200)
        self._min_h = wc.get("min_window_height", 200)
        self._off_screen = wc.get("off_screen_threshold", -1000)
        self._restore_pos = tuple(wc.get("restore_position", [100, 100]))

        # --- 列布局 ---
        cols = cfg.get("columns", {})
        self._col1_ratio = cols.get("col1_width_ratio", 0.153)
        self._col2_ratio = cols.get("col2_width_ratio", 0.238)
        self._bound_strip = cols.get("boundary_strip_width", 100)
        self._bound_y_start = cols.get("boundary_scan_start_ratio", 0.2)
        self._bound_y_end = cols.get("boundary_scan_end_ratio", 0.8)
        self._bound_limit = cols.get("boundary_search_limit", 80)
        self._bound_strong = cols.get("boundary_strong_threshold", 30)
        self._bound_weak = cols.get("boundary_weak_threshold", 15)

        # --- 裁切 ---
        crop = cfg.get("crop", {})
        ca = crop.get("chat_area", {})
        self._chat_rm_ratio = ca.get("right_margin_ratio", 0.30)
        self._chat_top_ratio = ca.get("top_crop_ratio", 0.12)
        self._chat_bot_ratio = ca.get("bottom_crop_ratio", 0.70)

        cp = crop.get("click_position", {})
        self._click_col2_x = cp.get("col2_row_x_ratio", 0.333)
        self._click_input_x = cp.get("input_box_x_ratio", 0.333)
        self._click_input_bot = cp.get("input_box_bottom_offset", 60)
        self._click_scroll_x = cp.get("scroll_center_x_ratio", 0.5)
        self._click_scroll_y = cp.get("scroll_center_y_ratio", 0.5)

        # --- 时序 ---
        tmg = cfg.get("timing", {})
        self._t_focus = tmg.get("focus_restore_delay", 0.3)
        self._t_click = tmg.get("click_before_delay", 0.1)

        self._hwnd: Optional[int] = None
        self._region: Optional[Tuple[int, int, int, int]] = None
        self._col2: Optional[Tuple[int, int, int, int]] = None
        self._col3: Optional[Tuple[int, int, int, int]] = None
        self.mouse = Mouse()
        self.kb = Kb()

    # ═══ 窗口管理 ═══════════════════════════

    def find_window(self) -> Optional[int]:
        if self._hwnd and self._is_valid(self._hwnd):
            return self._hwnd

        wxwork_pids = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info['name'] or ""
                if any(pn.lower() in name.lower()
                       for pn in self._process_names):
                    wxwork_pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not wxwork_pids:
            return None

        found = []

        def enum_cb(h, _):
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
                if pid not in wxwork_pids:
                    return
                cls = win32gui.GetClassName(h)
                title = win32gui.GetWindowText(h)
                if (cls == self._window_class
                        and len(title) > self._min_title_len
                        and win32gui.IsWindowVisible(h)):
                    found.append(h)
            except Exception:
                pass

        win32gui.EnumWindows(enum_cb, None)

        if found:
            self._hwnd = found[0]
            return self._hwnd
        return None

    def _is_valid(self, hwnd: int) -> bool:
        try:
            return win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd)
        except Exception:
            return False

    def focus(self):
        hwnd = self.find_window()
        if not hwnd:
            return False
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(self._t_focus)

            rect = win32gui.GetWindowRect(hwnd)
            if rect[0] < self._off_screen or rect[1] < self._off_screen:
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                win32gui.MoveWindow(
                    hwnd, self._restore_pos[0], self._restore_pos[1],
                    w, h, True)
                time.sleep(self._t_focus)
                logger.info(f"窗口移回屏幕: {self._restore_pos}")
            return True
        except Exception as e:
            logger.warning(f"focus失败: {e}")
            return False

    def is_running(self) -> bool:
        return self.find_window() is not None

    # ═══ 窗口区域 ═══════════════════════════

    def get_region(self) -> Optional[Tuple[int, int, int, int]]:
        hwnd = self.find_window()
        if not hwnd:
            return None
        try:
            rect = win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect
            w, h = right - left, bottom - top
            if (w > self._min_w and h > self._min_h
                    and left > self._off_screen
                    and top > self._off_screen):
                self._region = (left, top, w, h)
                return self._region
        except Exception:
            pass
        return self._region

    def get_col2_region(self) -> Optional[Tuple[int, int, int, int]]:
        r = self.get_region()
        if not r:
            return None

        col1_end = self._detect_col1_boundary(r)
        if col1_end is None:
            col1_end = r[0] + int(r[2] * self._col1_ratio)

        left, top, w, h = r
        col2_x = col1_end
        col2_w = int(w * self._col2_ratio)
        self._col2 = (col2_x, top, col2_w, h)
        return self._col2

    def get_col3_region(self) -> Optional[Tuple[int, int, int, int]]:
        r = self.get_region()
        c2 = self.get_col2_region()
        if not r or not c2:
            return None
        left, top, w, h = r
        col3_x = c2[0] + c2[2]
        col3_w = w - (col3_x - left)
        self._col3 = (col3_x, top, col3_w, h)
        return self._col3

    def _detect_col1_boundary(self, region) -> Optional[int]:
        try:
            left, top, w, h = region
            strip_w = min(self._bound_strip, w // 3)
            img = self.capture_region(left, top, strip_w, h)
            arr = np.array(img.convert('RGB'))

            y_s = int(h * self._bound_y_start)
            y_e = int(h * self._bound_y_end)
            region_arr = arr[y_s:y_e, :, :].astype(float)

            col_means = np.mean(region_arr, axis=0)
            diffs = np.sqrt(np.sum(
                (col_means[1:] - col_means[:-1]) ** 2, axis=1))

            last_boundary = None
            limit = min(self._bound_limit, len(diffs))
            for i in range(limit):
                if diffs[i] > self._bound_strong:
                    last_boundary = i
            if last_boundary is not None:
                return left + last_boundary + 1

            for i in range(limit):
                if diffs[i] > self._bound_weak:
                    last_boundary = i
            if last_boundary is not None:
                return left + last_boundary + 1
        except Exception:
            pass
        return None

    # ═══ 截图 ════════════════════════════

    def capture_screen(self, region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        with mss.mss() as sct:
            if region:
                monitor = {
                    "left": region[0], "top": region[1],
                    "width": region[2], "height": region[3],
                }
            else:
                monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            return Image.frombytes("RGB", screenshot.size, screenshot.rgb)

    def capture_region(self, left: int, top: int, width: int, height: int) -> Image.Image:
        return self.capture_screen((left, top, width, height))

    def capture_chat_list(self) -> Optional[Image.Image]:
        c2 = self.get_col2_region()
        if not c2:
            return None
        return self.capture_region(*c2)

    def capture_chat_area(self) -> Optional[Image.Image]:
        c3 = self.get_col3_region()
        if not c3:
            return None
        img = self.capture_region(*c3)
        cw, ch = img.size
        rm = int(cw * self._chat_rm_ratio)
        return img.crop((0, int(ch * self._chat_top_ratio),
                         cw - rm, int(ch * self._chat_bot_ratio)))

    # ═══ 点击 ════════════════════════════

    def click_position(self, x: int, y: int):
        self.mouse.position = (x, y)
        time.sleep(self._t_click)
        self.mouse.click(Button.left)

    def click_col2_row(self, y: int):
        c2 = self.get_col2_region()
        if not c2:
            return False
        self.click_position(c2[0] + int(c2[2] * self._click_col2_x), c2[1] + y)
        return True

    def click_input_box(self):
        c3 = self.get_col3_region()
        if not c3:
            return False
        input_x = c3[0] + int(c3[2] * self._click_input_x)
        input_y = c3[1] + c3[3] - self._click_input_bot
        self.click_position(input_x, input_y)
        return True

    def scroll_to_bottom(self):
        c3 = self.get_col3_region()
        if not c3:
            return
        cx = c3[0] + int(c3[2] * self._click_scroll_x)
        cy = c3[1] + int(c3[3] * self._click_scroll_y)
        self.mouse.position = (cx, cy)
        time.sleep(self._t_click)
        self.mouse.click(Button.left)
        time.sleep(0.3)  # 点击后等待焦点稳定再 Ctrl+End
        self.kb.press(Key.ctrl)
        self.kb.press(Key.end)
        self.kb.release(Key.end)
        self.kb.release(Key.ctrl)

    # ═══ 前台切换 + 键盘发送 ═══════════════

    def _switch_to_wecom_and_back(self, func):
        """Execute operation then switch back to user's window."""
        prev_window = pyautogui.getActiveWindow()
        prev_pos = pyautogui.position()
        wecom_windows = pyautogui.getWindowsWithTitle('企业微信')
        if not wecom_windows:
            logger.warning("未找到企业微信窗口，跳过操作")
            raise RuntimeError("未找到企业微信窗口")
        wecom_window = wecom_windows[0]
        try:
            wecom_window.activate()
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"切换前台失败: {e}，跳过操作")
            raise
        try:
            func()
        finally:
            try:
                if prev_window:
                    prev_window.activate()
                pyautogui.moveTo(prev_pos)
            except Exception:
                pass

    def send_message_via_keyboard(self, text: str):
        """Copy text to clipboard and send via Ctrl+V + Enter.

        Uses ctypes for direct clipboard access on Windows (no subprocess).
        """
        self._set_clipboard(text)
        # Click input box to ensure cursor is in the right place
        self.click_input_box()
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.1)
        pyautogui.press('enter')

    @staticmethod
    def _set_clipboard(text: str):
        """Set clipboard text using ctypes (no subprocess, no shell injection).

        All Win32 functions have restype/argtypes set to prevent 64-bit
        pointer truncation (default c_int return is 32-bit on Win64).
        """
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # ── Declare function signatures (critical for 64-bit) ──────────
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE

        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        if not user32.OpenClipboard(0):
            raise RuntimeError("Failed to open clipboard")
        try:
            user32.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                raise RuntimeError("GlobalAlloc failed")
            p = kernel32.GlobalLock(h)
            if not p:
                raise RuntimeError("GlobalLock failed (NULL pointer)")
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                raise RuntimeError("SetClipboardData failed")
        finally:
            user32.CloseClipboard()
