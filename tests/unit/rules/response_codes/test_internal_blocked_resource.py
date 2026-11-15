import pytest

from crawlforge.rules.response_codes.internal_blocked_resource import (
    InternalBlockedResource,
)
from crawlforge.storage.schema import init_database


@pytest.mark.skip(reason="awaiting schema page-vs-resource distinction")
def test_internal_blocked_resource():
    con = init_database()
    issues = list(InternalBlockedResource().check(con))
    assert issues == []


def test_rule_is_registered_but_disabled():
    assert InternalBlockedResource.enabled_by_default is False
