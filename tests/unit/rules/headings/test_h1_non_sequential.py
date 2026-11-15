import pytest

from crawlforge.rules.headings.h1_non_sequential import H1NonSequential
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    return init_database()


@pytest.mark.skip(reason="awaiting schema heading hierarchy data")
def test_flags_h1_after_other_headings(con):
    pass


def test_placeholder_returns_no_issues(con):
    issues = list(H1NonSequential().check(con))
    assert issues == []


def test_rule_disabled_by_default():
    assert H1NonSequential.enabled_by_default is False
