import pytest

from crawlforge.rules.response_codes.external_redirected import ExternalRedirected
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema externals not crawled")
def test_external_redirected():
    con = init_database()
    issues = list(ExternalRedirected().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert ExternalRedirected.enabled_by_default is False
