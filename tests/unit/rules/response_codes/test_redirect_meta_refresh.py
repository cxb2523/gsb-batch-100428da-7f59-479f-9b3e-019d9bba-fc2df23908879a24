import pytest

from crawlforge.rules.response_codes.redirect_meta_refresh import RedirectMetaRefresh
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema column redirects.redirect_type")
def test_meta_refresh_detection():
    con = init_database()
    issues = list(RedirectMetaRefresh().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert RedirectMetaRefresh.enabled_by_default is False
