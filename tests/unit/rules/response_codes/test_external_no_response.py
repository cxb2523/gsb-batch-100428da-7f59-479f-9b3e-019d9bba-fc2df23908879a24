import pytest

from crawlforge.rules.response_codes.external_no_response import ExternalNoResponse
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema externals not crawled")
def test_external_no_response():
    con = init_database()
    issues = list(ExternalNoResponse().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert ExternalNoResponse.enabled_by_default is False
