import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


def test_query_returns_entities(client):
    with patch("api.routes.query.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",), ("extracted_at",),
        ]
        mock_cursor.fetchall.return_value = [
            ("Google", "ORG", 0.9, "https://example.com/page",
             datetime(2026, 4, 12, tzinfo=timezone.utc),
             datetime(2026, 4, 12, tzinfo=timezone.utc))
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.post("/query", json={"query": "Google"})
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) >= 1
        assert body["data"][0]["entity_text"] == "Google"
        assert "sources" in body
        assert "confidence" in body


def test_query_empty_results(client):
    with patch("api.routes.query.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",), ("extracted_at",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.post("/query", json={"query": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["data"] == []


def test_query_missing_body(client):
    resp = client.post("/query", json={})
    assert resp.status_code == 422


def test_query_with_include_markdown(client):
    with patch("api.routes.query.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",), ("extracted_at",),
            ("markdown",), ("markdown_meta",),
        ]
        mock_cursor.fetchall.return_value = [
            ("Google", "ORG", 0.9, "https://example.com/page",
             datetime(2026, 4, 12, tzinfo=timezone.utc),
             datetime(2026, 4, 12, tzinfo=timezone.utc),
             "# Google\n\nSearch engine.",
             {"word_count": 20, "pipeline_version": "markdown-v1"})
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.post("/query", json={"query": "Google", "include_markdown": True})
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) >= 1
        assert body["data"][0]["markdown"] == "# Google\n\nSearch engine."
        assert body["data"][0]["markdown_meta"]["word_count"] == 20


def test_entity_by_id(client):
    with patch("api.routes.query.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "abc-123", "Google", "ORG", 0.9,
            "https://example.com", datetime(2026, 4, 12, tzinfo=timezone.utc),
        )
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.get("/entity/abc-123")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["entity_text"] == "Google"
