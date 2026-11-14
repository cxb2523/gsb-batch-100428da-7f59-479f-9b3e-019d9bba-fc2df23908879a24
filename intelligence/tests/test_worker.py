import json
import pytest
from datetime import datetime, timezone

from intelligence.worker import Worker
from intelligence.models import CrawlMessage


def test_parse_stream_message():
    raw = {
        "url": "https://example.com",
        "status_code": "200",
        "content_type": "text/html",
        "html": "<html>test</html>",
        "headers": json.dumps({"Server": "nginx"}),
        "fetched_at": "2026-04-12T12:00:00Z",
        "rendered": "false",
        "depth": "1",
    }

    msg = Worker.parse_message(raw)
    assert isinstance(msg, CrawlMessage)
    assert msg.url == "https://example.com"
    assert msg.status_code == 200
    assert msg.rendered is False
    assert msg.depth == 1
    assert msg.headers == {"Server": "nginx"}


def test_parse_stream_message_with_job_id():
    raw = {
        "url": "https://example.com",
        "status_code": "200",
        "content_type": "text/html",
        "html": "<html>test</html>",
        "headers": "{}",
        "fetched_at": "2026-04-12T12:00:00Z",
        "rendered": "false",
        "depth": "0",
        "job_id": "abc-123",
    }

    msg = Worker.parse_message(raw)
    assert msg.job_id == "abc-123"


def test_parse_message_missing_optional_fields():
    raw = {
        "url": "https://example.com",
        "status_code": "200",
        "content_type": "text/html",
        "html": "<html>test</html>",
        "headers": "{}",
        "fetched_at": "2026-04-12T12:00:00Z",
        "rendered": "false",
        "depth": "0",
    }

    msg = Worker.parse_message(raw)
    assert msg.job_id is None


def test_parse_message_handles_bytes_keys():
    """Redis without decode_responses returns bytes keys/values."""
    raw = {
        b"url": b"https://example.com",
        b"status_code": b"200",
        b"content_type": b"text/html",
        b"html": b"<html>test</html>",
        b"headers": b'{"Server": "nginx"}',
        b"fetched_at": b"2026-04-12T12:00:00Z",
        b"rendered": b"false",
        b"depth": b"1",
    }

    msg = Worker.parse_message(raw)
    assert msg.url == "https://example.com"
    assert msg.status_code == 200
    assert msg.headers == {"Server": "nginx"}
