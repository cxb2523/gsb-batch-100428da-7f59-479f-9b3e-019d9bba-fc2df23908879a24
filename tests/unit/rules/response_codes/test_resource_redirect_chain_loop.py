import pytest

from crawlforge.rules.response_codes.resource_redirect_chain_loop import (
    ResourceRedirectChainLoop,
)
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema resource-level redirect tracking")
def test_resource_redirect_chain_loop():
    con = init_database()
    issues = list(ResourceRedirectChainLoop().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert ResourceRedirectChainLoop.enabled_by_default is False
