import pytest

from crawlforge.rules.headings.h2_non_sequential import H2NonSequential
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    return init_database()


@pytest.mark.skip(reason="awaiting schema heading hierarchy data")
def test_flags_non_sequential_h2(con):
    pass


def test_placeholder_returns_no_issues(con):
    issues = list(H2NonSequential().check(con))
    assert issues == []


def test_rule_disabled_by_default():
    assert H2NonSequential.enabled_by_default is False
