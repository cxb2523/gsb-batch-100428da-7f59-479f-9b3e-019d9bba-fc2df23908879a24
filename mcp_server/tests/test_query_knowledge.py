from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from mcp_server.server import query_knowledge


class TestQueryKnowledge:
    """Tests for the query_knowledge MCP tool."""

    @patch("mcp_server.server.db")
    def test_returns_results_list(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchall.return_value = [
            ("Google", "ORG", 0.9, "https://example.com", datetime(2026, 1, 1)),
        ]
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = query_knowledge(query="Google", limit=10)

        assert "results" in result
        assert "total" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["entity_text"] == "Google"

    @patch("mcp_server.server.db")
    def test_empty_results(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = query_knowledge(query="nonexistent", limit=10)

        assert result["results"] == []
        assert result["total"] == 0

    @patch("mcp_server.server.db")
    def test_limit_clamped(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("entity_text",), ("entity_type",), ("confidence",),
            ("source_url",), ("fetched_at",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = query_knowledge(query="test", limit=999)
        # Should not error -- limit is clamped internally
        assert isinstance(result, dict)
