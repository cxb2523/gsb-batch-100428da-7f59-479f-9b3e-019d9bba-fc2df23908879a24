from __future__ import annotations

"""Unit tests for the ValidateEvents step."""

import pytest

from rpacore import BusinessException, ProcessContext, SystemException, Transaction

from steps import parse_timestamp
from steps.normalize_events import NormalizeEvents
from steps.validate_events import ValidateEvents


class TestValidateEvents:
    """Test the ValidateEvents step."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.transaction = Transaction(
            reference="test",
            state={"events": [
                {"event_id": "1", "event_type": "info", "timestamp": "2024-01-01T00:00:00Z", "source": "svc", "payload": {}},
                {"event_id": "2", "event_type": "error", "timestamp": "2024-01-02T00:00:00Z", "source": "svc", "payload": {}},
            ]},
        )
        self.ctx = ProcessContext(transaction=self.transaction)

    def test_passes_for_valid_events(self) -> None:
        step = ValidateEvents("validate_events", 2)
        step.execute(self.ctx)

    def test_raises_on_missing_event_id(self) -> None:
        self.transaction.state["events"] = [
            {"event_type": "info", "timestamp": "2024-01-01T00:00:00Z", "source": "svc"},
        ]
        self.transaction.metadata["event_count"] = 1
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "missing required field: event_id" in str(exc_info.value)
        assert "events" not in self.transaction.state
        assert "event_count" not in self.transaction.metadata

    def test_raises_on_missing_event_type(self) -> None:
        self.transaction.state["events"] = [
            {"event_id": "1", "timestamp": "2024-01-01T00:00:00Z", "source": "svc"},
        ]
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "missing required field: event_type" in str(exc_info.value)

    def test_raises_on_missing_timestamp(self) -> None:
        self.transaction.state["events"] = [
            {"event_id": "1", "event_type": "info", "source": "svc"},
        ]
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "missing required field: timestamp" in str(exc_info.value)

    def test_raises_on_missing_source(self) -> None:
        self.transaction.state["events"] = [
            {"event_id": "1", "event_type": "info", "timestamp": "2024-01-01T00:00:00Z"},
        ]
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "missing required field: source" in str(exc_info.value)

    def test_raises_on_invalid_event_type(self) -> None:
        self.transaction.state["events"] = [
            {"event_id": "1", "event_type": "critical", "timestamp": "2024-01-01T00:00:00Z", "source": "svc"},
        ]
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "invalid event_type" in str(exc_info.value)

    @pytest.mark.parametrize(
        "timestamp",
        [1704067200, True, 1.5, ["2024-01-01T00:00:00Z"], "not-a-timestamp", "2024-13-45T99:99:99Z"],
        ids=["epoch-int", "bool", "float", "list", "text", "impossible-date"],
    )
    def test_raises_on_non_iso_timestamp(self, timestamp) -> None:
        self.transaction.state["events"] = [
            {"event_id": "1", "event_type": "info", "timestamp": timestamp, "source": "svc"},
        ]
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "invalid timestamp" in str(exc_info.value)
        assert exc_info.value.code == "json_event_log.validation.invalid_event"
        assert "events" not in self.transaction.state

    @pytest.mark.parametrize(
        "timestamp",
        ["2024-01-01T00:00:00Z", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00"],
        ids=["utc-z", "utc-offset", "naive"],
    )
    def test_accepts_iso_timestamps(self, timestamp) -> None:
        self.transaction.state["events"] = [
            {"event_id": "1", "event_type": "info", "timestamp": timestamp, "source": "svc"},
        ]
        ValidateEvents("validate_events", 2).execute(self.ctx)

    def test_raises_on_empty_event_list(self) -> None:
        self.transaction.state["events"] = []
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "empty" in str(exc_info.value)

    def test_raises_when_no_events_in_context(self) -> None:
        self.transaction.state = {}
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "No events in context" in str(exc_info.value)

    def test_raises_on_non_dict_event(self) -> None:
        self.transaction.state["events"] = ["not a dict", {"event_id": "1"}]
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "is not an object" in str(exc_info.value)

    def test_raises_on_empty_string_required_field(self) -> None:
        self.transaction.state["events"] = [
            {"event_id": "", "event_type": "info", "timestamp": "2024-01-01T00:00:00Z", "source": "svc"},
        ]
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert "missing required field: event_id" in str(exc_info.value)

    def test_stop_true_causes_engine_skip_downstream(self) -> None:
        self.transaction.state["events"] = []
        step = ValidateEvents("validate_events", 2)
        with pytest.raises(BusinessException) as exc_info:
            step.execute(self.ctx)
        assert exc_info.value.halts_remaining_steps is True

    @pytest.mark.parametrize(
        "timestamp",
        [
            "2024-01-01T00:00:00Z",
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:00:00",
            "2024-01-01",
            20240101,
            1704067200,
            True,
            1.5,
            ["2024-01-01T00:00:00Z"],
            "not-a-timestamp",
            "2024-13-45T99:99:99Z",
            "0001-01-01T00:00:00+01:00",
            "9999-12-31T23:00:00-02:00",
        ],
    )
    def test_validation_accepts_exactly_what_normalization_can_parse(self, timestamp) -> None:
        """The two steps share one parser, so the accepted sets cannot drift.

        Checked in both directions: a value validation accepts must normalize,
        and a value validation rejects must not be normalizable either. A
        numeric basic-format value such as ``20240101`` is the case that catches
        a normalizer which coerces to text before parsing.
        """
        event = {"event_id": "1", "event_type": "info", "timestamp": timestamp, "source": "svc"}

        self.transaction.state["events"] = [event]
        try:
            ValidateEvents("validate_events", 2).execute(self.ctx)
            accepted = True
        except BusinessException:
            accepted = False

        normalize_tx = Transaction(reference="test", state={"events": [event]})
        try:
            NormalizeEvents("normalize_events", 3).execute(
                ProcessContext(transaction=normalize_tx)
            )
            normalized = True
        except SystemException:
            normalized = False

        assert accepted is normalized, (
            f"validation and normalization disagree on {timestamp!r}: "
            f"accepted={accepted} normalized={normalized}"
        )
        if accepted:
            assert parse_timestamp(timestamp) is not None
            assert normalize_tx.state["normalized_events"][0]["timestamp"]
