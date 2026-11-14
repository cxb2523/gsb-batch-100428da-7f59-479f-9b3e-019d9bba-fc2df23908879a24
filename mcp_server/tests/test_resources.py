import json
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

from mcp_server.server import get_stats, get_recent


class TestStatsResource:
    """Tests for the phantombot://stats resource."""

    @patch("mcp_server.server.db")
    def test_returns_stats_json(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (100, 500, 50, 90, 10, datetime(2026, 4, 12))
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_stats()
        data = json.loads(result)

        assert data["total_pages"] == 100
        assert data["total_entities"] == 500
        assert data["total_schemas"] == 50
        assert data["pages_with_markdown"] == 90
        assert data["domains_crawled"] == 10

    @patch("mcp_server.server.db")
    def test_handles_empty_database(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0, 0, 0, 0, 0, None)
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_stats()
        data = json.loads(result)

        assert data["total_pages"] == 0
        assert data["last_crawl_at"] is None


class TestRecentResource:
    """Tests for the phantombot://recent resource."""

    @patch("mcp_server.server.db")
    def test_returns_recent_pages(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("domain",), ("fetched_at",),
            ("has_markdown",), ("entity_count",), ("schema_count",),
        ]
        mock_cursor.fetchall.return_value = [
            (
                "page-1", "https://example.com/article", "example.com",
                datetime(2026, 4, 12), True, 5, 1,
            ),
        ]
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_recent()
        data = json.loads(result)

        assert "pages" in data
        assert len(data["pages"]) == 1
        assert data["pages"][0]["url"] == "https://example.com/article"
        assert data["pages"][0]["has_markdown"] is True

    @patch("mcp_server.server.db")
    def test_handles_empty_database(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id",), ("url",), ("domain",), ("fetched_at",),
            ("has_markdown",), ("entity_count",), ("schema_count",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg

        result = get_recent()
        data = json.loads(result)

        assert data["pages"] == []
