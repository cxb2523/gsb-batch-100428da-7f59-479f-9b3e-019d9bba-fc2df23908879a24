import pytest

from crawlforge.rules.meta_description.meta_desc_outside_head import MetaDescOutsideHead
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    return init_database()


@pytest.mark.skip(reason="awaiting schema column meta_description_in_head")
def test_flags_outside_head(con):
    pass


def test_placeholder_returns_no_issues(con):
    issues = list(MetaDescOutsideHead().check(con))
    assert issues == []


def test_rule_disabled_by_default():
    assert MetaDescOutsideHead.enabled_by_default is False
