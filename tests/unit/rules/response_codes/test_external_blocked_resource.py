import pytest

from crawlforge.rules.response_codes.external_blocked_resource import (
    ExternalBlockedResource,
)
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema externals not crawled")
def test_external_blocked_resource():
    con = init_database()
    issues = list(ExternalBlockedResource().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert ExternalBlockedResource.enabled_by_default is False
