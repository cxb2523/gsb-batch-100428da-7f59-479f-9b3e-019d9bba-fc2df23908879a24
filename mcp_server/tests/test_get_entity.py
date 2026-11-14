from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from mcp_server.server import get_entity


class TestGetEntity:
    """Tests for the get_entity MCP tool."""

    @patch("mcp_server.server.db")
    def test_returns_entity(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("entity_text",), ("entity_type",),
            ("confidence",), ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchone.return_value = (
            "abc-123", "Google", "ORG", 0.9,
            "https://example.com", datetime(2026, 1, 1),
        )
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_entity(entity_id="abc-123")

        assert result["entity_text"] == "Google"
        assert result["entity_type"] == "ORG"
        assert result["confidence"] == 0.9

    @patch("mcp_server.server.db")
    def test_not_found(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_entity(entity_id="nonexistent")

        assert "error" in result
