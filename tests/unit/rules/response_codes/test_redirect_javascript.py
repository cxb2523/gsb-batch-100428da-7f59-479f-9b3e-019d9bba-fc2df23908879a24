import pytest

from crawlforge.rules.response_codes.redirect_javascript import RedirectJavascript
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema column redirects.redirect_type")
def test_javascript_redirect_detection():
    con = init_database()
    issues = list(RedirectJavascript().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert RedirectJavascript.enabled_by_default is False
