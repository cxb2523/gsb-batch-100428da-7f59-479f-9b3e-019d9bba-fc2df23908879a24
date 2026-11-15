"""Smoke tests para reglas de Mobile."""

import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.mobile.content_not_sized_correctly import ContentNotSizedCorrectly
from crawlforge.rules.mobile.font_too_small_mobile import FontTooSmallMobile
from crawlforge.rules.mobile.image_map_present import ImageMapPresent
from crawlforge.rules.mobile.mobile_alternate_link import MobileAlternateLink
from crawlforge.rules.mobile.tap_targets_too_small import TapTargetsTooSmall
from crawlforge.rules.mobile.unsupported_plugins import UnsupportedPlugins
from crawlforge.rules.mobile.viewport_initial_scale_incorrect import (
    ViewportInitialScaleIncorrect,
)
from crawlforge.rules.mobile.viewport_initial_scale_missing import (
    ViewportInitialScaleMissing,
)
from crawlforge.rules.mobile.viewport_max_scale_set import ViewportMaxScaleSet
from crawlforge.rules.mobile.viewport_min_scale_set import ViewportMinScaleSet
from crawlforge.rules.mobile.viewport_multiple import ViewportMultiple
from crawlforge.rules.mobile.viewport_no_width import ViewportNoWidth
from crawlforge.rules.mobile.viewport_not_set import ViewportNotSet
from crawlforge.rules.mobile.viewport_specific_width import ViewportSpecificWidth
from crawlforge.rules.mobile.viewport_user_scalable_no import ViewportUserScalableNo
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, viewport
        # 1: NULL viewport (200 html) -> viewport_not_set
        (1, "https://a.com/no-vp",     200, "text/html", None),
        # 2: viewport without width
        (2, "https://a.com/no-w",      200, "text/html",
         "initial-scale=1"),
        # 3: viewport with specific px width
        (3, "https://a.com/specific",  200, "text/html",
         "width=320, initial-scale=1"),
        # 4: viewport with maximum-scale
        (4, "https://a.com/max",       200, "text/html",
         "width=device-width, initial-scale=1, maximum-scale=2"),
        # 5: viewport with minimum-scale
        (5, "https://a.com/min",       200, "text/html",
         "width=device-width, initial-scale=1, minimum-scale=0.5"),
        # 6: viewport with initial-scale != 1
        (6, "https://a.com/iscale",    200, "text/html",
         "width=device-width, initial-scale=0.8"),
        # 7: viewport without initial-scale
        (7, "https://a.com/no-iscale", 200, "text/html",
         "width=device-width"),
        # 8: viewport user-scalable=no
        (8, "https://a.com/locked",    200, "text/html",
         "width=device-width, initial-scale=1, user-scalable=no"),
        # 9: PERFECT viewport — should NOT trigger any rule (except initial-scale stuff that fits)
        (9, "https://a.com/ok",        200, "text/html",
         "width=device-width, initial-scale=1"),
        # 10: 404 page — ignored even with NULL viewport
        (10, "https://a.com/404",      404, "text/html", None),
        # 11: non-html content (JSON) — ignored
        (11, "https://a.com/api.json", 200, "application/json", None),
    ]
    for url_id, url, status, ctype, vp in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, viewport) "
            "VALUES (?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, vp],
        )
    return con


def test_viewport_not_set_flags_null_html_only(con):
    issues = list(ViewportNotSet().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1}


def test_viewport_not_set_ignores_404_and_non_html(con):
    issues = list(ViewportNotSet().check(con))
    flagged = {i.url_id for i in issues}
    assert 10 not in flagged
    assert 11 not in flagged


def test_viewport_no_width_flags_only_no_width(con):
    issues = list(ViewportNoWidth().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {2}


def test_viewport_specific_width_flags_px_width(con):
    issues = list(ViewportSpecificWidth().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {3}


def test_viewport_specific_width_does_not_flag_device_width(con):
    issues = list(ViewportSpecificWidth().check(con))
    flagged = {i.url_id for i in issues}
    assert 9 not in flagged
    assert 4 not in flagged


def test_viewport_max_scale_set_flags(con):
    issues = list(ViewportMaxScaleSet().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {4}


def test_viewport_min_scale_set_flags(con):
    issues = list(ViewportMinScaleSet().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {5}


def test_viewport_initial_scale_incorrect_flags_non_one(con):
    issues = list(ViewportInitialScaleIncorrect().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {6}


def test_viewport_initial_scale_incorrect_does_not_flag_one(con):
    issues = list(ViewportInitialScaleIncorrect().check(con))
    flagged = {i.url_id for i in issues}
    assert 9 not in flagged
    assert 4 not in flagged
    assert 8 not in flagged


def test_viewport_initial_scale_missing_flags(con):
    issues = list(ViewportInitialScaleMissing().check(con))
    flagged = {i.url_id for i in issues}
    # url 7 has no initial-scale; url 1 has NULL (excluded), url 2 has it
    assert 7 in flagged
    assert 1 not in flagged
    assert 9 not in flagged


def test_viewport_user_scalable_no_flags(con):
    issues = list(ViewportUserScalableNo().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {8}


def test_disabled_mobile_rules_yield_nothing(con):
    assert list(ContentNotSizedCorrectly().check(con)) == []
    assert list(FontTooSmallMobile().check(con)) == []
    assert list(UnsupportedPlugins().check(con)) == []
    assert list(TapTargetsTooSmall().check(con)) == []
    assert list(MobileAlternateLink().check(con)) == []
    assert list(ImageMapPresent().check(con)) == []
    assert list(ViewportMultiple().check(con)) == []


def test_disabled_mobile_rules_marked_disabled():
    for cls in (
        ContentNotSizedCorrectly,
        FontTooSmallMobile,
        UnsupportedPlugins,
        TapTargetsTooSmall,
        MobileAlternateLink,
        ImageMapPresent,
        ViewportMultiple,
    ):
        assert cls.enabled_by_default is False, cls.__name__


def test_all_mobile_rules_registered():
    import crawlforge.rules.mobile  # noqa: F401

    expected = {
        "viewport_not_set",
        "viewport_multiple",
        "viewport_no_width",
        "viewport_specific_width",
        "viewport_max_scale_set",
        "viewport_min_scale_set",
        "viewport_initial_scale_incorrect",
        "viewport_initial_scale_missing",
        "viewport_user_scalable_no",
        "content_not_sized_correctly",
        "font_too_small_mobile",
        "unsupported_plugins",
        "tap_targets_too_small",
        "mobile_alternate_link",
        "image_map_present",
    }
    for rid in expected:
        assert rid in REGISTRY, f"{rid} not registered"
