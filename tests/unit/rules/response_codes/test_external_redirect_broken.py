import pytest

from crawlforge.rules.response_codes.external_redirect_broken import (
    ExternalRedirectBroken,
)
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema externals not crawled")
def test_external_redirect_broken():
    con = init_database()
    issues = list(ExternalRedirectBroken().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert ExternalRedirectBroken.enabled_by_default is False
