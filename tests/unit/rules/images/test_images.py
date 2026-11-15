"""Smoke tests para reglas de Images."""

import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.images.image_alt_over_100_chars import ImageAltOver100Chars
from crawlforge.rules.images.image_alt_repeats_text import ImageAltRepeatsText
from crawlforge.rules.images.image_anchored_no_alt import ImageAnchoredNoAlt
from crawlforge.rules.images.image_background_a11y import ImageBackgroundA11y
from crawlforge.rules.images.image_incorrectly_sized import ImageIncorrectlySized
from crawlforge.rules.images.image_missing_alt_attribute import ImageMissingAltAttribute
from crawlforge.rules.images.image_missing_alt_text import ImageMissingAltText
from crawlforge.rules.images.image_missing_size_attrs import ImageMissingSizeAttrs
from crawlforge.rules.images.image_over_100kb import ImageOver100Kb
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    # Insert URLs (pages)
    con.execute(
        "INSERT INTO urls (url_id, url, status_code, content_type) VALUES "
        "(100, 'https://a.com/page1', 200, 'text/html'),"
        "(200, 'https://a.com/page2', 200, 'text/html')"
    )
    # Insert image resources covering all positive/negative cases.
    # Columns: source_url_id, resource_url, resource_type, status, size_bytes,
    #          alt, width, height, loading_attr, format
    rows = [
        # 1: alt empty string  -> missing_alt_text
        (100, "https://a.com/img-empty.jpg", "image", 200, 5000,
         "", 100, 100, "lazy", "jpg"),
        # 2: alt NULL          -> missing_alt_attribute
        (100, "https://a.com/img-noalt.jpg", "image", 200, 5000,
         None, 100, 100, "lazy", "jpg"),
        # 3: alt OK            -> negative case
        (100, "https://a.com/img-ok.jpg", "image", 200, 50_000,
         "A reasonable alt", 100, 100, "lazy", "jpg"),
        # 4: large image >100 KB -> over_100kb
        (100, "https://a.com/img-big.jpg", "image", 200, 250_000,
         "Big hero image", 1200, 800, "eager", "jpg"),
        # 5: alt > 100 chars   -> alt_over_100_chars
        (200, "https://a.com/img-longalt.jpg", "image", 200, 5000,
         "x" * 120, 100, 100, "lazy", "jpg"),
        # 6: missing width     -> missing_size_attrs
        (200, "https://a.com/img-nowidth.jpg", "image", 200, 5000,
         "alt", None, 100, "lazy", "jpg"),
        # 7: missing height    -> missing_size_attrs
        (200, "https://a.com/img-noheight.jpg", "image", 200, 5000,
         "alt", 100, None, "lazy", "jpg"),
        # 8: NOT an image (script) -> negative case for ALL image rules
        (200, "https://a.com/app.js", "script", 200, 250_000,
         None, None, None, None, "js"),
    ]
    for row in rows:
        con.execute(
            "INSERT INTO resources (source_url_id, resource_url, resource_type, "
            "status, size_bytes, alt, width, height, loading_attr, format) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(row),
        )
    return con


def test_missing_alt_text_flags_only_empty_alt(con):
    issues = list(ImageMissingAltText().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert urls == {"https://a.com/img-empty.jpg"}


def test_missing_alt_text_does_not_flag_null_or_ok(con):
    issues = list(ImageMissingAltText().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert "https://a.com/img-noalt.jpg" not in urls
    assert "https://a.com/img-ok.jpg" not in urls


def test_missing_alt_attribute_flags_only_null_alt(con):
    issues = list(ImageMissingAltAttribute().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert urls == {"https://a.com/img-noalt.jpg"}


def test_missing_alt_attribute_ignores_non_image_resources(con):
    issues = list(ImageMissingAltAttribute().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    # the JS resource has alt=NULL but resource_type='script'
    assert "https://a.com/app.js" not in urls


def test_image_over_100kb_flags_big_image(con):
    issues = list(ImageOver100Kb().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert urls == {"https://a.com/img-big.jpg"}


def test_image_over_100kb_ignores_small(con):
    issues = list(ImageOver100Kb().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert "https://a.com/img-ok.jpg" not in urls


def test_alt_over_100_chars_flags_long_alt(con):
    issues = list(ImageAltOver100Chars().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert urls == {"https://a.com/img-longalt.jpg"}


def test_alt_over_100_chars_ignores_short(con):
    issues = list(ImageAltOver100Chars().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert "https://a.com/img-ok.jpg" not in urls


def test_missing_size_attrs_flags_null_width_or_height(con):
    issues = list(ImageMissingSizeAttrs().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert urls == {
        "https://a.com/img-nowidth.jpg",
        "https://a.com/img-noheight.jpg",
        # img-noalt has both 100/100, ok-img also; missing are only the two above
    }


def test_missing_size_attrs_ignores_complete(con):
    issues = list(ImageMissingSizeAttrs().check(con))
    urls = {i.evidence["image_url"] for i in issues}
    assert "https://a.com/img-ok.jpg" not in urls


def test_disabled_rules_return_no_issues(con):
    """Rules awaiting schema columns should yield no issues."""
    assert list(ImageBackgroundA11y().check(con)) == []
    assert list(ImageIncorrectlySized().check(con)) == []
    assert list(ImageAnchoredNoAlt().check(con)) == []
    assert list(ImageAltRepeatsText().check(con)) == []


def test_disabled_rules_marked_disabled():
    assert ImageBackgroundA11y.enabled_by_default is False
    assert ImageIncorrectlySized.enabled_by_default is False
    assert ImageAnchoredNoAlt.enabled_by_default is False
    assert ImageAltRepeatsText.enabled_by_default is False


def test_all_image_rules_registered():
    import crawlforge.rules.images  # noqa: F401

    expected = {
        "image_missing_alt_text",
        "image_missing_alt_attribute",
        "image_background_a11y",
        "image_over_100kb",
        "image_alt_over_100_chars",
        "image_incorrectly_sized",
        "image_missing_size_attrs",
        "image_anchored_no_alt",
        "image_alt_repeats_text",
    }
    for rid in expected:
        assert rid in REGISTRY, f"{rid} not registered"
