import pytest

from crawlforge.rules.response_codes.external_5xx import External5xx
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema externals not crawled")
def test_external_5xx():
    con = init_database()
    issues = list(External5xx().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert External5xx.enabled_by_default is False
