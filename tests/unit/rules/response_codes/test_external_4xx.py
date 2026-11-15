import pytest

from crawlforge.rules.response_codes.external_4xx import External4xx
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema externals not crawled")
def test_external_4xx():
    con = init_database()
    issues = list(External4xx().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert External4xx.enabled_by_default is False
