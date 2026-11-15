import pytest

from crawlforge.rules.headings.heading_levels_skip import HeadingLevelsSkip
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    return init_database()


@pytest.mark.skip(reason="awaiting schema heading hierarchy data")
def test_flags_skipped_levels(con):
    pass


def test_placeholder_returns_no_issues(con):
    issues = list(HeadingLevelsSkip().check(con))
    assert issues == []


def test_rule_disabled_by_default():
    assert HeadingLevelsSkip.enabled_by_default is False
