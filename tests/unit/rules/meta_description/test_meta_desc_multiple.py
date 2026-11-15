import pytest

from crawlforge.rules.meta_description.meta_desc_multiple import MetaDescMultiple
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    return init_database()


@pytest.mark.skip(reason="awaiting schema column meta_description_count")
def test_flags_multiple(con):
    pass


def test_placeholder_returns_no_issues(con):
    """Until schema tracks multiple meta descriptions, the rule must not fire."""
    issues = list(MetaDescMultiple().check(con))
    assert issues == []


def test_rule_disabled_by_default():
    assert MetaDescMultiple.enabled_by_default is False
