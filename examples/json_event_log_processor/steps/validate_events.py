from __future__ import annotations

from rpacore import BusinessException, ProcessContext, Step

from steps import ALLOWED_EVENT_TYPES, parse_timestamp

REQUIRED_FIELDS = ("event_id", "event_type", "timestamp", "source")


class ValidateEvents(Step):
    """Validate that all events in the loaded data conform to the expected schema."""

    def _reject(self, ctx: ProcessContext, message: str) -> None:
        ctx.state.pop("events", None)
        ctx.transaction.metadata.pop("event_count", None)
        raise BusinessException(
            message,
            action=self.name,
            halts_remaining_steps=True,
            code="json_event_log.validation.invalid_event",
        )

    def _timestamp_is_acceptable(self, value: object) -> bool:
        """Reject a truthy non-string or unparseable timestamp as bad input.

        Sharing ``parse_timestamp`` with ``NormalizeEvents`` keeps the accepted
        set identical, so a value that passes here cannot fail later as a
        retryable system error.
        """
        try:
            parse_timestamp(value)
        except (TypeError, ValueError):
            return False
        return True

    def execute(self, ctx: ProcessContext) -> None:
        events = ctx.optional_state("events", list, None, action=self.name)

        if events is None:
            self._reject(
                ctx,
                "No events in context — load_json_file must run first",
            )
        if not isinstance(events, list):
            self._reject(
                ctx,
                f"Expected a list of events, got {type(events).__name__}",
            )
        if not events:
            self._reject(ctx, "Event list is empty")

        for i, event in enumerate(events):
            if not isinstance(event, dict):
                self._reject(
                    ctx,
                    f"Event at index {i} is not an object (got {type(event).__name__})",
                )
            for field in REQUIRED_FIELDS:
                if field not in event or not event[field]:
                    self._reject(
                        ctx,
                        f"Event at index {i} (id={event.get('event_id', 'unknown')}) "
                        f"missing required field: {field}",
                    )
            if event["event_type"] not in ALLOWED_EVENT_TYPES:
                self._reject(
                    ctx,
                    f"Event at index {i} (id={event.get('event_id', 'unknown')}) "
                    f"has invalid event_type: {event['event_type']!r} "
                    f"(expected one of {ALLOWED_EVENT_TYPES})",
                )
            if not self._timestamp_is_acceptable(event["timestamp"]):
                self._reject(
                    ctx,
                    f"Event at index {i} (id={event.get('event_id', 'unknown')}) "
                    f"has invalid timestamp: {event['timestamp']!r} "
                    f"(expected an ISO 8601 string)",
                )
