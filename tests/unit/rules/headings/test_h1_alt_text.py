import pytest

from crawlforge.rules.headings.h1_alt_text import H1AltText
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    return init_database()


@pytest.mark.skip(reason="awaiting schema differentiation between text and alt in h1")
def test_flags_h1_made_only_of_alt_text(con):
    pass


def test_placeholder_returns_no_issues(con):
    issues = list(H1AltText().check(con))
    assert issues == []


def test_rule_disabled_by_default():
    assert H1AltText.enabled_by_default is False
