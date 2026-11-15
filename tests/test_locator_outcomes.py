"""Local, offline verification of LocatorImage failure outcomes.

No real desktop, screenshot or GUI is required: the locator dependencies are
injected (fake locate function, image map, clock and sleep).

Run with::

    python -m pytest tests/test_locator_outcomes.py -q
"""

import pytest

from rpapy.core.locator_outcome import LocatorOutcome


class FakeClock:
    """Monotonic fake clock: time only advances when sleep() is called."""

    def __init__(self, start=0.0):
        self.value = float(start)

    def time(self):
        return self.value

    def sleep(self, seconds):
        self.value += float(seconds)


def make_images(image_name='btn_ok', image_path='/fake/images/btn_ok-(0, 0, 100, 100).png'):
    return {
        image_name: {
            'image': image_path,
            'region': (0, 0, 100, 100),
            'anchor_coord': None,
        }
    }


def make_locator(clock, images, locate_func, **kwargs):
    from rpapy.core.locator import LocatorImage

    max_wait = kwargs.pop('max_wait', None)
    interval = kwargs.pop('interval', None)
    maintenance_mode = kwargs.pop('maintenance_mode', False)

    locator = LocatorImage(
        images,
        locate_func=locate_func,
        map_images_func=lambda: images,
        time_func=clock.time,
        sleep_func=clock.sleep,
        center_func=lambda box: (box[0] + (box[2] - box[0]) // 2,
                                 box[1] + (box[3] - box[1]) // 2),
        maintenance_mode=maintenance_mode,
        move_mouse=False,
        show_func=lambda path: None,
        **kwargs,
    )
    if max_wait is not None:
        locator.max_wait = max_wait
    if interval is not None:
        locator.interval = interval
    return locator


def test_success_returns_coordinate_tuple():
    """The success path keeps returning an (x, y) tuple with displacement."""
    clock = FakeClock()
    images = make_images()
    locator = make_locator(clock, images, lambda **kw: (10, 20, 30, 40))

    result = locator('btn_ok', deslocar_x=5, deslocar_y=-5)

    assert isinstance(result, tuple)
    assert result == (25, 25)


def test_success_uses_anchor_when_no_displacement():
    clock = FakeClock()
    images = make_images()
    images['btn_ok']['anchor_coord'] = (7, 9)
    locator = make_locator(clock, images, lambda **kw: (10, 20, 30, 40))

    assert locator('btn_ok') == (27, 39)


def test_timeout_not_found_returns_outcome_with_details():
    clock = FakeClock()
    images = make_images()
    locator = make_locator(
        clock, images, lambda **kw: None,
        max_wait=3,
        screenshot_func=lambda: _FakeImage(),
        error_save_func=lambda image_name, im: '/fake/error_images/x.png',
        interval=0.5,
    )

    outcome = locator('btn_ok', max_wait=3)

    assert isinstance(outcome, LocatorOutcome)
    assert outcome.kind == LocatorOutcome.TIMEOUT_NOT_FOUND
    assert outcome.image_name == 'btn_ok'
    assert outcome.image_path == images['btn_ok']['image']
    assert outcome.elapsed_seconds >= 3
    assert outcome.elapsed_seconds < 3 + 0.5 + 1e-9
    assert outcome.error_image_path == '/fake/error_images/x.png'
    # An outcome is falsy so it can never be mistaken for a coordinate.
    assert not outcome


def test_ignore_error_returns_outcome_and_does_not_raise():
    clock = FakeClock()
    images = make_images()
    calls = []

    def failing_screenshot():
        calls.append('screenshot')
        raise AssertionError('screenshot must not be taken when ignore_error')

    locator = make_locator(
        clock, images, lambda **kw: None,
        max_wait=2, interval=0.5,
        screenshot_func=failing_screenshot,
    )

    outcome = locator('btn_ok', max_wait=2, ignore_error=True)

    assert isinstance(outcome, LocatorOutcome)
    assert outcome.kind == LocatorOutcome.TIMEOUT_NOT_FOUND
    assert calls == []
    assert outcome.error_image_path is None


def test_wait_vanish_timeout_returns_outcome():
    clock = FakeClock()
    images = make_images()
    locator = make_locator(
        clock, images, lambda **kw: (10, 20, 30, 40),
        max_wait=2, interval=0.5,
    )

    outcome = locator('btn_ok', max_wait=2, wait_vanish=True)

    assert isinstance(outcome, LocatorOutcome)
    assert outcome.kind == LocatorOutcome.WAIT_VANISH_TIMEOUT
    assert outcome.image_name == 'btn_ok'
    assert outcome.image_path == images['btn_ok']['image']
    assert outcome.wait_vanish is True
    assert outcome.elapsed_seconds >= 2


def test_wait_vanish_success_returns_none():
    clock = FakeClock()
    images = make_images()
    locator = make_locator(clock, images, lambda **kw: None)

    assert locator('btn_ok', wait_vanish=True) is None


def test_maintenance_cancel_returns_immediate_outcome_and_does_not_loop():
    """A cancelled re-capture ends the lookup immediately (no endless loop)."""
    clock = FakeClock()
    images = make_images()
    update_calls = []

    def cancelled_update(image_path):
        update_calls.append(image_path)
        return False

    locator = make_locator(
        clock, images, lambda **kw: None,
        maintenance_mode=True,
        update_image_func=cancelled_update,
        max_wait=1, interval=0.25,
    )

    outcome = locator('btn_ok', max_wait=1)

    assert isinstance(outcome, LocatorOutcome)
    assert outcome.kind == LocatorOutcome.MAINTENANCE_ABORTED
    assert outcome.image_name == 'btn_ok'
    assert outcome.image_path == images['btn_ok']['image']
    assert outcome.maintenance_mode is True
    assert len(update_calls) == 1


def test_maintenance_none_return_also_aborts():
    clock = FakeClock()
    images = make_images()
    locator = make_locator(
        clock, images, lambda **kw: None,
        maintenance_mode=True,
        update_image_func=lambda path: None,
        max_wait=1, interval=0.25,
    )

    outcome = locator('btn_ok', max_wait=1)
    assert outcome.kind == LocatorOutcome.MAINTENANCE_ABORTED


def test_maintenance_recapture_resets_timer():
    """After a re-capture the wait restarts; pre-capture time is not counted."""
    clock = FakeClock()
    images = make_images()
    state = {'recaptures': 0, 'found': False}

    def locate(**kwargs):
        return (10, 20, 30, 40) if state['found'] else None

    def update_image(path):
        state['recaptures'] += 1
        # Simulate the user taking some time to re-capture.
        clock.sleep(2.0)
        if state['recaptures'] == 1:
            images['btn_ok']['image'] = '/fake/images/btn_ok_v2.png'
            return True
        state['found'] = True
        return True

    locator = make_locator(
        clock, images, locate,
        maintenance_mode=True,
        update_image_func=update_image,
        max_wait=1, interval=0.25,
    )

    result = locator('btn_ok', max_wait=1)

    assert result == (20, 30)
    assert state['recaptures'] >= 1
    assert images['btn_ok']['image'] == '/fake/images/btn_ok_v2.png'


def test_maintenance_recapture_remaps_and_reports_new_path():
    """On abort after a successful re-capture, the reported path is the new one."""
    clock = FakeClock()
    images = make_images()
    calls = {'count': 0}

    def update_image(path):
        calls['count'] += 1
        if calls['count'] == 1:
            images['btn_ok']['image'] = '/fake/images/btn_ok_new.png'
            return True
        # Second prompt: the user cancels.
        return False

    locator = make_locator(
        clock, images, lambda **kw: None,
        maintenance_mode=True,
        update_image_func=update_image,
        max_wait=1, interval=0.25,
    )

    outcome = locator('btn_ok', max_wait=1)

    assert outcome.kind == LocatorOutcome.MAINTENANCE_ABORTED
    assert outcome.image_path == '/fake/images/btn_ok_new.png'
    assert calls['count'] == 2


def test_activities_stops_when_outcome_is_returned(monkeypatch):
    """get_element_vision must not click/type using an outcome as a coordinate."""
    import pyautogui

    import rpapy.activities as activities

    calls = []

    class FakeLocator:
        def __call__(self, image_name, **kwargs):
            return LocatorOutcome(
                LocatorOutcome.TIMEOUT_NOT_FOUND, image_name, '/fake/x.png',
                elapsed_seconds=2.0,
            )

    def fake_click(*args, **kwargs):
        calls.append(('click', args))

    def fake_press(*args, **kwargs):
        calls.append(('press', args))

    monkeypatch.setattr(pyautogui, 'click', fake_click)
    monkeypatch.setattr(pyautogui, 'press', fake_press)

    result = activities.get_element_vision(
        'btn_ok', attr_name='click', identifier_img=FakeLocator(),
        execute=True, ignore_error=True,
    )

    assert isinstance(result, LocatorOutcome)
    assert calls == []


def test_activities_raises_image_not_found_when_ignore_error_false():
    import rpapy.activities as activities
    from rpapy.core.snipps.snippingtools import (ImageNotDisappearError,
                                                 ImageNotFoundError)

    class FakeLocator:
        def __init__(self, kind):
            self.kind = kind

        def __call__(self, image_name, **kwargs):
            return LocatorOutcome(self.kind, image_name, '/fake/x.png',
                                  elapsed_seconds=1.0)

    with pytest.raises(ImageNotFoundError) as excinfo:
        activities.get_element_vision(
            'btn_ok', attr_name='click', identifier_img=FakeLocator(LocatorOutcome.TIMEOUT_NOT_FOUND),
            execute=True,
        )
    assert excinfo.value.outcome.kind == LocatorOutcome.TIMEOUT_NOT_FOUND

    with pytest.raises(ImageNotDisappearError):
        activities.get_element_vision(
            'btn_ok', identifier_img=FakeLocator(LocatorOutcome.WAIT_VANISH_TIMEOUT),
            wait_vanish=True, ignore_error=False,
        )


def test_activities_maintenance_abort_is_visible():
    import rpapy.activities as activities
    from rpapy.core.snipps.snippingtools import ImageNotFoundError

    class FakeLocator:
        def __call__(self, image_name, **kwargs):
            return LocatorOutcome(LocatorOutcome.MAINTENANCE_ABORTED,
                                  image_name, '/fake/x.png',
                                  elapsed_seconds=0.0)

    with pytest.raises(ImageNotFoundError):
        activities.get_element_vision('btn_ok', identifier_img=FakeLocator())


def test_ignore_error_does_not_raise_in_locator_even_without_screenshot():
    """ignore_error=True never raises, even if the screenshot step would fail."""
    clock = FakeClock()
    images = make_images()
    locator = make_locator(
        clock, images, lambda **kw: None,
        max_wait=1, interval=0.25,
        screenshot_func=lambda: (_ for _ in ()).throw(RuntimeError('boom')),
    )

    outcome = locator('btn_ok', max_wait=1, ignore_error=True)
    assert isinstance(outcome, LocatorOutcome)


def test_error_screenshot_is_saved_on_plain_timeout():
    """The diagnostic screenshot path is reported in the outcome."""
    clock = FakeClock()
    images = make_images()
    saved = []

    locator = make_locator(
        clock, images, lambda **kw: None,
        max_wait=1, interval=0.25,
        screenshot_func=lambda: _FakeImage(),
        error_save_func=lambda image_name, im: saved.append(image_name) or '/fake/error.png',
    )

    outcome = locator('btn_ok', max_wait=1)

    assert outcome.kind == LocatorOutcome.TIMEOUT_NOT_FOUND
    assert outcome.error_image_path == '/fake/error.png'
    assert saved == ['btn_ok']


def test_maintenance_wait_vanish_alert_dismissed_returns_outcome():
    """Dismissing the 'did not disappear' alert must end the wait, not loop."""
    clock = FakeClock()
    images = make_images()
    alerts = []

    def alert_func(*, title, text):
        alerts.append(text)
        return None

    locator = make_locator(
        clock, images, lambda **kw: (10, 20, 30, 40),
        maintenance_mode=True,
        alert_func=alert_func,
        max_wait=1, interval=0.25,
    )

    outcome = locator('btn_ok', max_wait=1, wait_vanish=True)

    assert isinstance(outcome, LocatorOutcome)
    assert outcome.kind == LocatorOutcome.MAINTENANCE_ABORTED
    assert len(alerts) == 1


def test_maintenance_wait_vanish_alert_confirmed_restarts_timer():
    """Confirming the alert restarts the wait instead of failing immediately."""
    clock = FakeClock()
    images = make_images()
    state = {'alerts': 0}

    def alert_func(*, title, text):
        state['alerts'] += 1
        return 'OK'

    def locate(**kwargs):
        # Disappears only after the alert was acknowledged.
        return (10, 20, 30, 40) if state['alerts'] == 0 else None

    locator = make_locator(
        clock, images, locate,
        maintenance_mode=True,
        alert_func=alert_func,
        max_wait=1, interval=0.25,
    )

    result = locator('btn_ok', max_wait=1, wait_vanish=True)

    assert result is None
    assert state['alerts'] == 1


def test_missing_image_name_does_not_loop_forever():
    """An image that cannot even be created must produce an outcome."""
    clock = FakeClock()
    images = {}

    locator = make_locator(
        clock, {}, lambda **kw: None,
        max_wait=1, interval=0.25,
    )
    # Keep the placeholder creation from writing/interacting with the disk.
    locator.map_images_func = lambda: images
    locator._ensure_default_template = lambda image_name: False

    outcome = locator('ghost', max_wait=1)

    assert isinstance(outcome, LocatorOutcome)
    assert outcome.kind == LocatorOutcome.TIMEOUT_NOT_FOUND
    assert outcome.image_name == 'ghost'


def test_raise_on_error_keeps_legacy_exception_behaviour():
    """raise_on_error=True still raises the legacy exceptions."""
    from rpapy.core.snipps.snippingtools import (ImageNotDisappearError,
                                                 ImageNotFoundError)

    clock = FakeClock()
    images = make_images()

    appear_locator = make_locator(
        clock, images, lambda **kw: None, max_wait=1, interval=0.25,
        error_save_func=lambda name, im: None,
    )
    with pytest.raises(ImageNotFoundError):
        appear_locator('btn_ok', max_wait=1, raise_on_error=True)

    vanish_locator = make_locator(
        clock, images, lambda **kw: (10, 20, 30, 40), max_wait=1, interval=0.25,
    )
    with pytest.raises(ImageNotDisappearError):
        vanish_locator('btn_ok', max_wait=1, wait_vanish=True, raise_on_error=True)


def test_outcome_unpacking_raises_legacy_exception():
    """Generated ``*xy`` patterns must not silently unpack an outcome."""
    from rpapy.core.snipps.snippingtools import (ImageNotDisappearError,
                                                 ImageNotFoundError)

    outcome = LocatorOutcome(LocatorOutcome.TIMEOUT_NOT_FOUND, 'btn_ok',
                             '/fake/x.png')
    with pytest.raises(ImageNotFoundError):
        x, y = outcome

    vanish_outcome = LocatorOutcome(LocatorOutcome.WAIT_VANISH_TIMEOUT,
                                    'btn_ok', '/fake/x.png')
    with pytest.raises(ImageNotDisappearError):
        list(vanish_outcome)


def test_outcome_kind_accepts_aliases():
    outcome = LocatorOutcome('timeout', 'btn_ok', '/fake/x.png')
    assert outcome.kind == 'timeout_not_found'
    assert outcome.kind == LocatorOutcome.TIMEOUT_NOT_FOUND
    assert outcome == 'timeout'
    assert outcome.to_dict()['image_name'] == 'btn_ok'


class _FakeImage:
    def save(self, *args, **kwargs):
        return None


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
