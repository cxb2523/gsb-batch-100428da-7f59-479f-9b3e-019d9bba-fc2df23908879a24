import pytest

from crawlforge.rules.meta_description.meta_desc_below_400_pixels import (
    MetaDescBelow400Pixels,
)
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    return init_database()


@pytest.mark.skip(reason="awaiting schema column meta_description_pixels")
def test_flags_below_400_pixels(con):
    pass


def test_placeholder_returns_no_issues(con):
    issues = list(MetaDescBelow400Pixels().check(con))
    assert issues == []


def test_rule_disabled_by_default():
    assert MetaDescBelow400Pixels.enabled_by_default is False
