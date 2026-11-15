import pytest

from crawlforge.rules.response_codes.resource_redirected import ResourceRedirected
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema resource-level redirect tracking")
def test_resource_redirected():
    con = init_database()
    issues = list(ResourceRedirected().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert ResourceRedirected.enabled_by_default is False
