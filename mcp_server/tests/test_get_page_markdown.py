from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from mcp_server.server import get_page_markdown


class TestGetPageMarkdown:
    """Tests for the get_page_markdown MCP tool."""

    @patch("mcp_server.server.db")
    def test_returns_markdown(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("markdown",), ("markdown_meta",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "page-1",
            "https://example.com/article",
            "# Article Title\n\nContent here...",
            {"word_count": 50, "has_code": False, "has_tables": False, "pipeline_version": "markdown-v1"},
            datetime(2026, 1, 1),
        )
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_page_markdown(page_id="page-1")

        assert result["url"] == "https://example.com/article"
        assert "# Article Title" in result["markdown"]
        assert result["meta"]["word_count"] == 50

    @patch("mcp_server.server.db")
    def test_page_not_found(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_page_markdown(page_id="nonexistent")

        assert "error" in result

    @patch("mcp_server.server.db")
    def test_no_markdown_available(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("markdown",), ("markdown_meta",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "page-1", "https://example.com", None, None, datetime(2026, 1, 1),
        )
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_page_markdown(page_id="page-1")

        assert "error" in result
