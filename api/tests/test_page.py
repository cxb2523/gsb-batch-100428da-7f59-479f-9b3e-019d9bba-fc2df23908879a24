import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


def test_get_page_markdown_returns_content(client):
    with patch("api.routes.page.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("markdown",), ("markdown_meta",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "page-uuid-1234",
            "https://example.com/article",
            "# Article Title\n\nContent here...",
            {"word_count": 50, "has_code": False, "has_tables": False, "pipeline_version": "markdown-v1", "confidence": 0.9},
            datetime(2026, 4, 12, tzinfo=timezone.utc),
        )
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.get("/page/page-uuid-1234/markdown")
        assert resp.status_code == 200

        body = resp.json()
        assert body["data"]["url"] == "https://example.com/article"
        assert "# Article Title" in body["data"]["markdown"]
        assert body["data"]["meta"]["word_count"] == 50
        assert "sources" in body
        assert body["sources"] == ["https://example.com/article"]


def test_get_page_markdown_not_found(client):
    with patch("api.routes.page.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.get("/page/nonexistent-id/markdown")
        assert resp.status_code == 404
        assert "Page not found" in resp.json()["detail"]


def test_get_page_markdown_no_markdown_available(client):
    with patch("api.routes.page.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("markdown",), ("markdown_meta",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "page-uuid-5678",
            "https://example.com/no-markdown",
            None,
            None,
            datetime(2026, 4, 12, tzinfo=timezone.utc),
        )
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.get("/page/page-uuid-5678/markdown")
        assert resp.status_code == 404
        assert "Markdown not available" in resp.json()["detail"]
