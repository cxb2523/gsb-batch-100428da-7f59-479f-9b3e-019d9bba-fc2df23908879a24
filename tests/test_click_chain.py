'''
Tests for the Click execution chain: hit, miss and oversized-target cases.

All images are generated locally as solid-color pictures; no real desktop,
screenshot or mouse is involved. pyautogui.screenshot is monkeypatched to
write the prepared screen image, and a fake AutoGuiWrapper records the
commands that would have been executed.
'''
import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from detect.img_detector import ImgDetector, MATCH_THRESHOLD
from script import tor_runner
from script.tor_runner import RunScript


SCREEN_W, SCREEN_H = 300, 200
PATCH_W, PATCH_H = 20, 20
PATCH_X, PATCH_Y = 120, 80  # top-left of the target patch on the screen


class FakeAuto:
    '''Records commands instead of driving a real mouse/keyboard.'''

    def __init__(self):
        self.clicks = []
        self.types = []
        self.presses = []
        self.waits = []

    def click(self, coord):
        self.clicks.append(coord)

    def type(self, string):
        self.types.append(string)

    def press(self, key_name):
        self.presses.append(key_name)

    def wait(self, sec):
        self.waits.append(sec)


def make_screen(with_patch=True):
    screen = np.full((SCREEN_H, SCREEN_W), 100, np.uint8)
    if with_patch:
        screen[PATCH_Y:PATCH_Y + PATCH_H, PATCH_X:PATCH_X + PATCH_W] = 255
    return screen


def save_img(path, img):
    cv2.imwrite(str(path), img)


def make_workspace(tmp_path, target_img, script_text):
    '''Create a workspace with icon.png and script.tor, return its path.'''
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    save_img(workspace / 'icon.png', target_img)
    (workspace / 'script.tor').write_text(script_text)
    return str(workspace)


def run_workspace(monkeypatch, workspace, screen_img):
    '''Run script.tor in workspace against the given fake screen.'''
    monkeypatch.setattr(
        'detect.img_detector.pyautogui.screenshot',
        lambda path: save_img(path, screen_img))
    auto = FakeAuto()
    runner = RunScript(workspace, auto=auto)
    result = runner.exec_script(workspace + '/script.tor')
    return result, auto


# ---------------------------------------------------------------- detector

def test_detector_hit_returns_expected_center(tmp_path):
    target = np.full((PATCH_H, PATCH_W), 255, np.uint8)
    target_path = tmp_path / 'icon.png'
    save_img(target_path, target)

    detector = ImgDetector(str(tmp_path))
    coord = detector.img_coord_detector(str(target_path), make_screen())

    expected = (PATCH_X + PATCH_W / 2, PATCH_Y + PATCH_H / 2)
    assert coord == expected


def test_detector_miss_returns_none(tmp_path):
    target = np.full((PATCH_H, PATCH_W), 255, np.uint8)
    target_path = tmp_path / 'icon.png'
    save_img(target_path, target)

    detector = ImgDetector(str(tmp_path))
    coord = detector.img_coord_detector(
        str(target_path), make_screen(with_patch=False))

    assert coord is None


def test_detector_oversized_target_returns_none(tmp_path):
    target = np.full((SCREEN_H + 50, SCREEN_W + 50), 255, np.uint8)
    target_path = tmp_path / 'icon.png'
    save_img(target_path, target)

    detector = ImgDetector(str(tmp_path))
    coord = detector.img_coord_detector(str(target_path), make_screen())

    assert coord is None


# ------------------------------------------------------- execution chain

def test_runner_hit_clicks_and_finishes_script(monkeypatch, tmp_path):
    target = np.full((PATCH_H, PATCH_W), 255, np.uint8)
    workspace = make_workspace(
        tmp_path, target,
        'Click(icon)\nType(hello)\nPress(enter)\n')

    result, auto = run_workspace(monkeypatch, workspace, make_screen())

    assert result.success is True
    assert auto.clicks == [(PATCH_X + PATCH_W / 2, PATCH_Y + PATCH_H / 2)]
    assert auto.types == ['hello']
    assert auto.presses == ['enter']


def test_runner_miss_skips_click_and_aborts(monkeypatch, tmp_path):
    target = np.full((PATCH_H, PATCH_W), 255, np.uint8)
    workspace = make_workspace(
        tmp_path, target,
        'Type(before)\nClick(icon)\nType(after)\nPress(enter)\n')

    result, auto = run_workspace(
        monkeypatch, workspace, make_screen(with_patch=False))

    # The failure is identifiable: line number and command text are reported.
    assert result.success is False
    assert result.line_no == 2
    assert result.command_text == 'Click(icon)'
    assert result.reason

    # The click was skipped and nothing after the failing line ran.
    assert auto.clicks == []
    assert auto.types == ['before']
    assert auto.presses == []


def test_runner_oversized_target_aborts(monkeypatch, tmp_path):
    target = np.full((SCREEN_H + 50, SCREEN_W + 50), 255, np.uint8)
    workspace = make_workspace(
        tmp_path, target,
        'Click(icon)\nType(after)\n')

    result, auto = run_workspace(monkeypatch, workspace, make_screen())

    assert result.success is False
    assert result.line_no == 1
    assert result.command_text == 'Click(icon)'
    assert auto.clicks == []
    assert auto.types == []
