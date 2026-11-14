from unittest.mock import patch, MagicMock
import pytest


def test_map_returns_urls_from_db(client):
    with patch("api.routes.sitemap.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("https://example.com/page1",),
            ("https://example.com/page2",),
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.post("/map", json={"url": "https://example.com", "include_sitemap": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] >= 2
        assert body["data"]["from_database"] == 2


def test_map_invalid_url(client):
    resp = client.post("/map", json={"url": ""})
    assert resp.status_code == 422


def test_map_with_search_filter(client):
    with patch("api.routes.sitemap.get_pg_connection") as mock_pg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("https://example.com/api/docs",)]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.post("/map", json={"url": "https://example.com", "search": "api", "include_sitemap": False})
        assert resp.status_code == 200
