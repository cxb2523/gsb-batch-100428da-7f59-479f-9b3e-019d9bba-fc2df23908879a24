"""The drag path against a REAL engine, on a self-made HTML page.

The unit half (`test_a_drag_presses_once_and_rechecks_its_target.py`) proves the
shape of the loop with a fake engine; this proves the page-observable contract
the requirement states, on the browser the events actually reach:

  1. a normal drag succeeds and the page receives exactly ONE `mousedown`,
     paired with exactly one `mouseup`, and the moves between them carry
     `buttons === 1`;
  2. a target that moves on the press is re-resolved and re-hit inside the
     timeout, still with one press;
  3. a target an overlay covers and uncovers while the button is held is
     retried until it is reachable - again one press;
  4. a target that STAYS covered ends in an error naming the last reason (the
     overlay), not in a bare timeout, and the press and the release are still
     paired; and a move made afterwards reports `buttons === 0`.

The page is `tests/gates/drag_recheck_harness.html`: one draggable source and
one target that moves or gains an overlay in its `mousedown` handler - i.e.
only AFTER the source has been pressed, which is the window this whole change
is about. It is served over loopback by an in-process HTTP server, so nothing
here leaves the machine.

Needs the patched firefox binary: `INVPW_BINARY_PATH=<firefox.exe>`. Without
one the tests skip, the same contract every e2e file in this repo keeps.
"""
from __future__ import annotations

import http.server
import os
import pathlib
import socketserver
import threading

import pytest

pytestmark = pytest.mark.e2e

_HARNESS = (pathlib.Path(__file__).with_name("gates")
            / "drag_recheck_harness.html")


def _binary() -> str:
    path = os.environ.get("INVPW_BINARY_PATH", "")
    if not path or not os.path.isfile(path):
        pytest.skip("INVPW_BINARY_PATH names no firefox binary")
    return path


class _QuietServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture(scope="module")
def harness_url():
    page = _HARNESS.read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            pass

    server = _QuietServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _, port = server.server_address
    try:
        yield "http://127.0.0.1:%d/drag.html" % port
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def browser_page(tmp_path):
    from invisible_core import build_launch_env
    from invisible_core._fpforge import generate_profile
    from invisible_core.juggler.instance import launch
    from invisible_core.prefs import compose_session_prefs

    profile = generate_profile(seed=20261115)
    prefs = dict(compose_session_prefs(
        profile, locale="en-US", timezone="America/New_York").prefs)
    browser = launch(_binary(), prefs=prefs,
                     profile_dir=str(tmp_path / "profile"),
                     env=build_launch_env(prefs), headless=True,
                     ready_timeout=120)
    try:
        context = browser.default_context()
        page = context.new_page()
        yield page
    finally:
        browser.close()


def _arm(page, mode, **extra):
    """Tell the page what its source's mousedown handler should do."""
    assigns = "window.__harness.mode = %r;" % mode
    assigns += "".join(" window.__harness.%s = %s;" % (k, v)
                       for k, v in extra.items())
    page.injected.evaluate(page.main_frame_id, assigns)


def _harness(page):
    return page.injected.evaluate(
        page.main_frame_id, "window.__harness")


def _press_and_release_indices(events):
    """The positions of the one press and the one release, in event order."""
    down = next(i for i, e in enumerate(events) if e["type"] == "mousedown")
    up = next(i for i, e in enumerate(events) if e["type"] == "mouseup")
    return down, up


def _press_and_release_counts(events):
    downs = [e for e in events if e["type"] == "mousedown"]
    ups = [e for e in events if e["type"] == "mouseup"]
    return downs, ups


def test_a_normal_drag_drops_once_and_the_page_sees_one_mousedown(
        browser_page, harness_url):
    page = browser_page
    page.lifecycle.goto(harness_url, timeout=60)

    page.actions.drag_and_drop("#source", "#target", timeout=30)

    h = _harness(page)
    events = h["events"]
    downs, ups = _press_and_release_counts(events)
    assert len(downs) == 1, "the page saw %d mousedowns for one drag" % len(downs)
    assert len(ups) == 1, "press and release are not paired"
    assert h["drops"] == 1
    down_i, up_i = _press_and_release_indices(events)
    held = [e["buttons"] for e in events[down_i + 1:up_i]
            if e["type"] == "mousemove"]
    assert held and all(b == 1 for b in held), (
        "a move of the drag did not carry buttons=1: %r" % held)


def test_a_target_that_moves_on_the_press_is_rehit_with_one_press(
        browser_page, harness_url):
    page = browser_page
    page.lifecycle.goto(harness_url, timeout=60)
    _arm(page, "move")

    page.actions.drag_and_drop("#source", "#target", timeout=30)

    h = _harness(page)
    assert h["moved"] is True
    assert h["drops"] == 1, "the release never followed the drifting target"
    downs, ups = _press_and_release_counts(h["events"])
    assert len(downs) == 1
    assert len(ups) == 1


def test_a_transient_overlay_is_outwaited_without_a_second_press(
        browser_page, harness_url):
    page = browser_page
    page.lifecycle.goto(harness_url, timeout=60)
    # The overlay appears on the press and clears itself 400 ms later, while
    # the driver is still inside the deadline re-resolving and re-checking.
    _arm(page, "cover", overlayClearsAt=400)

    page.actions.drag_and_drop("#source", "#target", timeout=30)

    h = _harness(page)
    assert h["covered"] is True
    assert h["drops"] == 1
    downs, ups = _press_and_release_counts(h["events"])
    assert len(downs) == 1
    assert len(ups) == 1


def test_a_permanent_overlay_raises_the_reason_and_leaves_no_button_down(
        browser_page, harness_url):
    page = browser_page
    page.lifecycle.goto(harness_url, timeout=60)
    _arm(page, "cover", overlayClearsAt=0)

    from invisible_core.juggler.actions import ElementNotActionable

    with pytest.raises(ElementNotActionable) as exc:
        page.actions.drag_and_drop("#source", "#target", timeout=3)
    # Not a bare timeout: the last turn's hit verdict names the overlay.
    assert "overlay" in str(exc.value).lower()

    h = _harness(page)
    downs, ups = _press_and_release_counts(h["events"])
    assert len(downs) == 1, "a retry pressed the source again"
    assert len(ups) == 1, "the failure left the mouse button down"
    assert h["drops"] == 0

    # The button state is cleared: a move made afterwards reports buttons=0.
    before = len(h["events"])
    page.actions.move(20, 20)
    after = _harness(page)
    new_moves = [e for e in after["events"][before:] if e["type"] == "mousemove"]
    assert new_moves and all(e["buttons"] == 0 for e in new_moves), (
        "the next move still carried the held button: %r" % new_moves)
