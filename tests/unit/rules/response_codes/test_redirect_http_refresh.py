import pytest

from crawlforge.rules.response_codes.redirect_http_refresh import RedirectHttpRefresh
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema column redirects.redirect_type")
def test_http_refresh_detection():
    con = init_database()
    issues = list(RedirectHttpRefresh().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert RedirectHttpRefresh.enabled_by_default is False
