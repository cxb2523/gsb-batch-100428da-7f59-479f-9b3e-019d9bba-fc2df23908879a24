"""A drag presses its source ONCE; after the press, its target is re-resolved,
re-pointed and hit-checked on every turn until the release.

⛔ THE DEFECT THIS GUARDS. The far end of a drag is only known after the press,
and the page can change in that window - a list reflowing, a kanban card
moving, an overlay sliding in. Two shapes were both wrong:

  * releasing on the point measured first silently lands the `mouseup` on
    whatever moved under the pointer, so the drop is lost and nobody is told;
  * running the WHOLE gesture over inside the retry loop presses the source a
    second time, and the page sees two `mousedown`s - a repeat no hand makes.

The shape that is neither: the source is resolved and pressed exactly once,
outside every loop; the target end is a retry loop over what remains of the
action's own deadline, and every one of its turns re-resolves the selector,
takes the point again and hit-checks it BEFORE the release. A failed hit is a
retryable condition that starts another turn without another press. A target
that never becomes reachable ends as an `ElementNotActionable` carrying the
reason of the last turn, not as a bare timeout.

⛔ AND FAILURE NEVER LEAVES THE MOUSE BUTTON DOWN. A target timeout, a hit that
keeps failing, or any exception in between is followed by exactly one release
and `_pressed` is cleared, so the `buttons` field of the next click is zero
again. Down and up are paired on every path.

The companion of the shape, on the generic loop, is `test_juggler_actions.py`;
the single-press invariant for ordinary clicks is `test_a_click_is_delivered_once.py`;
the meaning of the `buttons` mask itself is `test_a_drag_keeps_its_buttons_pressed.py`.
The real-browser half of this file's acceptance lives in
`test_a_drag_reaches_a_target_that_moves_e2e.py` and runs only with an engine.
"""
from __future__ import annotations

import pytest
from _closed_world import check_command

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.actions import ActionMissed, Actions, ElementNotActionable

pytestmark = pytest.mark.unit

MAIN = "frame-main"
SOURCE = "source-node"
TARGET = "target-node"
SOURCE_POINT = (100.0, 100.0)
TARGET_POINT = (400.0, 300.0)
MOVED_TARGET_POINT = (620.0, 140.0)
OVERLAY = "<div id='overlay'>"


def _quad(point):
    x, y = point
    return {"p1": {"x": x - 10, "y": y - 8}, "p2": {"x": x + 10, "y": y - 8},
            "p3": {"x": x + 10, "y": y + 8}, "p4": {"x": x - 10, "y": y + 8}}


class _Page:
    """A page with one draggable source and one target.

    Everything the driver asks is answered from THIS state, so each test
    describes a page rather than a list of canned verdicts:

      * the target has a point that can drift (`move_target`) and a hit verdict
        that can be covered by an overlay (`cover_target`);
      * `on_down` runs when the `mousedown` is handled - the moment after which
        a real page starts rearranging itself;
      * the connection records every mouse event the engine would deliver, so
        the page itself can count the presses it received.
    """

    def __init__(self, *, target_present=True, target_visible=True,
                 hittable=True, on_down=None, target_point=TARGET_POINT):
        self.target_present = target_present
        self.target_visible = target_visible
        self.hittable = hittable
        self.on_down = on_down or (lambda page: None)
        self.target_point = target_point
        self.mousedowns = 0
        self.mouseups = 0
        self.drops = 0
        self.landed: dict = {}
        #: The point each mouse event was dispatched at, in order.
        self.event_points: list = []

    def move_target(self, point=MOVED_TARGET_POINT):
        self.target_point = point

    def cover_target(self):
        self.hittable = False

    def note_landing(self, kind, landed):
        """What the engine answered for an event that named its element."""
        self.landed[kind] = landed
        if kind == "mouseup" and landed:
            self.drops += 1


class _Connection:
    """The engine half: answers the geometry, dispatches the mouse events and
    says, in the dispatch's own answer, where an event naming its element
    LANDED (`landsOn` -> `landing`, since firefox-39 [B230])."""

    def __init__(self, page: _Page):
        self.page = page
        self.sent: list = []

    def send(self, method, params=None, session=None, timeout=None):
        # Closed world, like the engine: a field the protocol mirror does not
        # declare is a rejected command, not an ignored one.
        check_command(method, params)
        params = params or {}
        self.sent.append((method, dict(params)))
        if method == "Page.getContentQuads":
            if params.get("objectId") == TARGET and not self.page.target_visible:
                return {"quads": []}
            point = (SOURCE_POINT if params.get("objectId") == SOURCE
                     else self.page.target_point)
            return {"quads": [_quad(point)]}
        if method == "Page.dispatchMouseEvent":
            kind = params["type"]
            self.page.event_points.append((params["x"], params["y"], kind))
            if kind == "mousedown":
                self.page.mousedowns += 1
                self.page.on_down(self.page)
            elif kind == "mouseup":
                self.page.mouseups += 1
            if "landsOn" not in params:
                return {}
            landed = self.page.hittable
            self.page.note_landing(kind, landed)
            return {"landing": {"type": kind, "landed": landed, "seen": 1,
                                "on": "" if landed else OVERLAY}}
        return {}

    def mouse_events(self):
        return [(p["type"], p["buttons"]) for m, p in self.sent
                if m == "Page.dispatchMouseEvent"]


class _Injected:
    def __init__(self, page: _Page):
        self.page = page
        self.hit_checks = 0
        self.target_resolves = 0
        self.disposed: list = []

    def query_selector(self, frame, selector, strict=False):
        if selector == "#source":
            return SOURCE
        self.target_resolves += 1
        return TARGET if self.page.target_present else None

    def element_states(self, frame, element, states):
        if element == TARGET and not self.page.target_visible:
            return {"ok": False, "missing": "visible"}
        return {"ok": True}

    def check_hit_target(self, frame, element, point):
        if element != TARGET:
            return "done"
        self.hit_checks += 1
        return "done" if self.page.hittable else OVERLAY

    def scroll_into_view(self, frame, element, rect=None):
        return None

    def dispose(self, frame, element):
        self.disposed.append(element)

    def evaluate(self, frame, expression, **kw):
        return {"w": 1280, "h": 900}


class _Keyboard:
    def modifier_mask(self):
        return 0


def _actions(page: _Page):
    actions = Actions.__new__(Actions)
    actions.lifecycle = type("_Lifecycle", (), {"main_frame": MAIN})()
    actions.inj = _Injected(page)
    actions.c = _Connection(page)
    actions.session = "s"
    actions.keyboard = _Keyboard()
    actions.position = (0.0, 0.0)
    # No persona and no motion generator: one event per glide, and no real
    # sleeps. The rhythm is not what these tests are about.
    actions.pointer_persona = None
    actions.typing_persona = None
    actions.input_latency = None
    actions.motion = None
    actions.motion_budget_s = None
    actions.engine_approach = False
    actions.acts = PageActs(1)
    actions._pressed = 0
    actions._landing_target = None
    return actions


def _types(actions):
    return [t for t, _ in actions.c.mouse_events()]


# ── the normal path ─────────────────────────────────────────────────────────

def test_a_normal_drag_presses_once_releases_once_and_drops():
    """The control arm: a target that stays put is hit on the first turn, and
    the page receives exactly one press and one release, in that order."""
    page = _Page()
    actions = _actions(page)

    result = actions.drag_and_drop("#source", "#target", timeout=2.0)

    assert result == TARGET_POINT
    assert page.mousedowns == 1, "the source was pressed more than once"
    assert page.mouseups == 1
    assert page.drops == 1
    kinds = _types(actions)
    assert kinds.count("mousedown") == 1
    assert kinds.count("mouseup") == 1
    assert kinds[0] == "mousemove"          # the approach onto the source
    assert kinds.index("mousedown") < kinds.index("mouseup")
    # The release lands on the target's point, not the source's.
    assert page.event_points[-1] == (*TARGET_POINT, "mouseup")


def test_every_move_between_press_and_release_carries_the_held_button():
    page = _Page()
    actions = _actions(page)

    actions.drag_and_drop("#source", "#target", timeout=2.0)

    events = actions.c.mouse_events()
    down = next(i for i, (t, _) in enumerate(events) if t == "mousedown")
    up = next(i for i, (t, _) in enumerate(events) if t == "mouseup")
    assert all(buttons == 1 for t, buttons in events[down:up] if t == "mousemove")
    assert events[up] == ("mouseup", 0)


# ── the target moves after the press ────────────────────────────────────────

def test_a_target_that_drifts_is_repointed_and_hit_without_a_second_press():
    """⛔ THE DRIFT ARM. On `mousedown` the page moves the target: the first
    point is stale before the release. The loop re-resolves, takes the new
    point, re-checks it and releases THERE - still with the single press.

    The re-point is observable directly: the quad is read again after the
    move (the fake's quads always answer the target's CURRENT geometry), so
    the release's coordinates are the moved ones, not the ones measured first.
    """
    page = _Page(on_down=lambda p: p.move_target())
    actions = _actions(page)

    actions.drag_and_drop("#source", "#target", timeout=2.0)

    assert page.mousedowns == 1
    assert page.mouseups == 1
    assert page.drops == 1, "the release never followed the target"
    assert page.event_points[-1] == (*MOVED_TARGET_POINT, "mouseup")
    # The glide approached the NEW point: no event was ever sent to the point
    # the target had before it moved.
    assert not any(point == TARGET_POINT for point, _, kind
                   in page.event_points if kind == "mousemove")
    assert actions.inj.hit_checks >= 1


def test_a_target_covered_then_uncovered_is_retried_until_it_is_hit():
    """The hit check fails on the first turns (an overlay is covering the
    target) and then passes. Every failed turn is a retry, and none of them
    presses again."""
    page = _Page(on_down=lambda p: p.cover_target())
    actions = _actions(page)

    # The overlay clears itself on the page's own clock after two failed hit
    # checks: the check-count latch is the page-side timer in miniature.
    original_check = actions.inj.check_hit_target

    def recovering(frame, element, point):
        verdict = original_check(frame, element, point)
        if actions.inj.hit_checks >= 3:
            page.hittable = True
        return verdict

    actions.inj.check_hit_target = recovering

    actions.drag_and_drop("#source", "#target", timeout=2.0)

    assert page.mousedowns == 1
    assert page.mouseups == 1
    assert page.drops == 1
    assert actions.inj.hit_checks >= 2, "the failed turn was not retried"
    assert _types(actions).count("mousedown") == 1


def test_a_target_that_never_becomes_hittable_raises_its_last_reason():
    """⛔ NOT A BARE TIMEOUT. The overlay never clears: the action ends with
    the last turn's readable reason, the press still happened exactly once,
    and a release pairs it before the exception leaves."""
    page = _Page(on_down=lambda p: p.cover_target())
    actions = _actions(page)

    with pytest.raises(ElementNotActionable) as exc:
        actions.drag_and_drop("#source", "#target", timeout=0.35)

    message = str(exc.value)
    assert "overlay" in message, message
    assert "Last reason" in message
    assert page.mousedowns == 1, "a retry pressed the source again"
    assert page.mouseups == 1, "the failure left the button down"
    assert page.drops == 0


def test_a_target_that_vanishes_names_that_reason():
    """A selector that stops matching anything is its own last-turn reason,
    distinct from a covered one."""
    page = _Page(on_down=lambda p: setattr(p, "target_present", False))
    actions = _actions(page)

    with pytest.raises(ElementNotActionable, match="finds nothing"):
        actions.drag_and_drop("#source", "#target", timeout=0.35)

    assert page.mousedowns == 1
    assert page.mouseups == 1


def test_a_target_that_goes_invisible_names_the_missing_state():
    page = _Page(on_down=lambda p: setattr(p, "target_visible", False))
    actions = _actions(page)

    with pytest.raises(ElementNotActionable, match="missing visible"):
        actions.drag_and_drop("#source", "#target", timeout=0.35)

    assert page.mousedowns == 1
    assert page.mouseups == 1


# ── failures clean up the button state ──────────────────────────────────────

def test_after_a_failed_drag_the_buttons_mask_is_cleared():
    """The next click's `buttons` field must not still say "pressed"."""
    page = _Page(on_down=lambda p: p.cover_target())
    actions = _actions(page)

    with pytest.raises(ElementNotActionable):
        actions.drag_and_drop("#source", "#target", timeout=0.35)

    assert actions._pressed == 0
    # A later move reports zero held buttons.
    actions.move(5, 5)
    assert actions.c.mouse_events()[-1] == ("mousemove", 0)


def test_a_failure_while_travelling_still_releases_and_clears():
    """An exception raised by the glide itself, mid-gesture, is not allowed to
    skip the cleanup."""
    page = _Page()
    actions = _actions(page)
    real_glide = actions._glide

    def glide(point, **kw):
        # The source's own approach is allowed; the travel TOWARD THE TARGET,
        # which is the one made with the button held, breaks.
        if kw.get("buttons") == 1:
            raise RuntimeError("the pipe broke mid-travel")
        return real_glide(point, **kw)

    actions._glide = glide

    with pytest.raises(RuntimeError, match="pipe broke"):
        actions.drag_and_drop("#source", "#target", timeout=2.0)

    assert page.mousedowns == 1
    assert page.mouseups == 1, "no release followed the mid-travel failure"
    assert actions._pressed == 0


def test_a_held_foreign_button_is_restored_not_dropped():
    """If another button was already down when the drag started, the cleanup
    leaves that one exactly as it found it and only lets the left one up."""
    page = _Page(on_down=lambda p: p.cover_target())
    actions = _actions(page)
    actions._pressed = 2  # the right button was already held

    with pytest.raises(ElementNotActionable):
        actions.drag_and_drop("#source", "#target", timeout=0.35)

    assert actions._pressed == 2
    # The corrective release still describes zero buttons AFTER it, as every
    # release does; it is the in-memory mask that keeps the foreign one.
    assert actions.c.mouse_events()[-1] == ("mouseup", 0)


def test_a_release_that_misses_is_reported_not_retried_and_not_repeated():
    """The hit check passes and the release goes out naming the target, but the
    engine reports it landed elsewhere (the page moved in the last gap). That
    is an `ActionMissed`: reported, not retried - the release cannot be un-sent
    - and the mouse is not released a second time."""
    page = _Page()
    actions = _actions(page)
    original_send = actions.c.send

    def send(method, params=None, **kw):
        answer = original_send(method, params, **kw)
        # The pre-release hit check keeps passing; only the engine's landing
        # answer for the mouseup says the target moved away in the final gap.
        if (method == "Page.dispatchMouseEvent"
                and (params or {}).get("type") == "mouseup"
                and "landsOn" in (params or {})):
            page.hittable = False
            return {"landing": {"type": "mouseup", "landed": False,
                                "seen": 1, "on": OVERLAY}}
        return answer

    actions.c.send = send

    with pytest.raises(ActionMissed, match="mouseup landed on"):
        actions.drag_and_drop("#source", "#target", timeout=2.0)

    assert page.mousedowns == 1
    assert page.mouseups == 1, "the miss was followed by a second release"
    assert actions._pressed == 0


# ── the deadline is one budget ───────────────────────────────────────────────

def test_the_target_loop_runs_on_the_time_left_not_a_new_budget(monkeypatch):
    """The source's resolve and the target's retries share ONE deadline: a
    target that cannot be hit must fail inside the action's own timeout rather
    than opening a second, equally long budget while the button is held."""
    import invisible_core.juggler.actions as actions_mod

    made: list = []
    real_deadline = actions_mod.Deadline

    class _SpyingDeadline(real_deadline):
        def __init__(self, seconds):
            made.append(seconds)
            super().__init__(seconds)

    monkeypatch.setattr(actions_mod, "Deadline", _SpyingDeadline)
    page = _Page(on_down=lambda p: p.cover_target())
    actions = _actions(page)

    with pytest.raises(ElementNotActionable):
        actions.drag_and_drop("#source", "#target", timeout=0.3)

    # Exactly one Deadline for the whole gesture: source loop and target loop
    # share it rather than each constructing their own.
    assert made == [0.3]
    assert page.mousedowns == 1
    assert page.mouseups == 1


# ── the options the action takes ─────────────────────────────────────────────

def test_force_drops_wherever_the_pointer_is_without_a_hit_check():
    """`force` means "send it where the pointer is", exactly as it does for a
    click: an overlay over the target is the request, not a miss. The hit check
    is skipped and the release goes out regardless."""
    page = _Page(on_down=lambda p: p.cover_target())
    actions = _actions(page)

    actions.drag_and_drop("#source", "#target", timeout=2.0, force=True)

    assert actions.inj.hit_checks == 0
    assert page.mousedowns == 1
    # The release named the target and the engine said it landed on the
    # overlay; `force` skips the landing gate too, so the call succeeds.
    assert page.mouseups == 1
    assert actions._pressed == 0


def test_trial_checks_both_ends_and_sends_no_mouse_events():
    page = _Page()
    actions = _actions(page)

    assert actions.drag_and_drop("#source", "#target", timeout=2.0,
                                 trial=True) is None

    assert actions.c.mouse_events() == []
    assert page.mousedowns == 0


def test_the_drag_works_with_an_engine_drawn_approach():
    """The other pointer mode (`engine_approach=True`, the Selenium/Puppeteer
    side): the glide draws a path instead of one event, and the gesture still
    pairs one press with one release and drops."""
    page = _Page()
    actions = _actions(page)
    actions.engine_approach = True
    # A motion generator would need a persona; without one `_glide` still
    # emits the single destination event, which is the documented fallback.
    assert actions.motion is None

    actions.drag_and_drop("#source", "#target", timeout=2.0)

    assert page.mousedowns == 1
    assert page.mouseups == 1
    assert page.drops == 1
    assert actions._pressed == 0
