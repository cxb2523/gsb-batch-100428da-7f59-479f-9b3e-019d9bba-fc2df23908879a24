import pytest

from crawlforge.rules.meta_description.meta_desc_over_985_pixels import (
    MetaDescOver985Pixels,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    return init_database()


@pytest.mark.skip(reason="awaiting schema column meta_description_pixels")
def test_flags_over_985_pixels(con):
    pass


def test_placeholder_returns_no_issues(con):
    issues = list(MetaDescOver985Pixels().check(con))
    assert issues == []


def test_rule_disabled_by_default():
    assert MetaDescOver985Pixels.enabled_by_default is False
