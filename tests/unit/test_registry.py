import pytest

from crawlforge.rules._issue import Issue
from crawlforge.rules._registry import REGISTRY, Rule, register_rule


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = dict(REGISTRY)
    REGISTRY.clear()
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def test_register_rule_adds_to_registry():
    @register_rule
    class DummyRule(Rule):
        id = "dummy_rule"
        name = "Dummy Rule"
        category = "Test"
        severity = "info"
        description = "Test rule."

        def check(self, con):
            return iter([])

    assert "dummy_rule" in REGISTRY
    assert REGISTRY["dummy_rule"] is DummyRule


def test_register_rejects_missing_id():
    with pytest.raises(ValueError, match="must set class attribute `id`"):

        @register_rule
        class _NoId(Rule):
            name = "No id"
            category = "Test"
            severity = "info"

            def check(self, con):
                return iter([])


def test_register_rejects_invalid_severity():
    with pytest.raises(ValueError, match="severity"):

        @register_rule
        class _BadSeverity(Rule):
            id = "bad_sev"
            name = "Bad"
            category = "Test"
            severity = "panic"  # type: ignore[assignment]

            def check(self, con):
                return iter([])


def test_register_rejects_duplicate_id():
    @register_rule
    class _First(Rule):
        id = "dup"
        name = "First"
        category = "Test"
        severity = "info"

        def check(self, con):
            return iter([])

    with pytest.raises(ValueError, match="Duplicate rule id"):

        @register_rule
        class _Second(Rule):
            id = "dup"
            name = "Second"
            category = "Test"
            severity = "info"

            def check(self, con):
                return iter([])


def test_issue_id_is_stable():
    a = Issue(rule_id="r", severity="info", category="c", message="m", url_id=1)
    b = Issue(rule_id="r", severity="info", category="c", message="m", url_id=1)
    assert a.issue_id == b.issue_id


def test_issue_to_row_serializes_evidence_as_json():
    issue = Issue(
        rule_id="r",
        severity="warning",
        category="c",
        message="m",
        evidence={"url": "https://a.com/", "n": 3},
    )
    row = issue.to_row()
    assert isinstance(row["evidence"], str)
    assert "https://a.com/" in row["evidence"]
    assert row["issue_id"] == issue.issue_id
