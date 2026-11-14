import uuid
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.server import crawl_url


class TestCrawlUrl:
    """Tests for the crawl_url MCP tool."""

    @patch("mcp_server.server.db")
    def test_valid_url_returns_job_id(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (uuid.uuid4(),)
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg
        mock_db.get_redis_client.return_value = MagicMock()

        result = crawl_url(url="https://example.com", max_depth=3)

        assert "job_id" in result
        assert result["url"] == "https://example.com"
        assert result["status"] == "pending"

    @patch("mcp_server.server.db")
    def test_invalid_scheme_raises(self, mock_db):
        result = crawl_url(url="ftp://example.com", max_depth=3)
        assert "error" in result

    @patch("mcp_server.server.db")
    def test_no_scheme_raises(self, mock_db):
        result = crawl_url(url="example.com", max_depth=3)
        assert "error" in result

    @patch("mcp_server.server.db")
    def test_max_depth_clamped(self, mock_db):
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (uuid.uuid4(),)
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.get_pg_connection.return_value = mock_pg
        mock_db.get_redis_client.return_value = MagicMock()

        result = crawl_url(url="https://example.com", max_depth=99)
        assert "job_id" in result  # Should clamp, not error
