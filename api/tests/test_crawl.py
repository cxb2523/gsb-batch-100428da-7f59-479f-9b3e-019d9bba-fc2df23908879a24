import pytest
from unittest.mock import patch, MagicMock


def test_crawl_valid_url(client):
    with patch("api.routes.crawl.get_redis_client") as mock_redis, \
         patch("api.routes.crawl.get_pg_connection") as mock_pg:
        mock_rdb = MagicMock()
        mock_redis.return_value = mock_rdb

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("test-job-id",)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.post("/crawl", json={"url": "https://example.com"})
        assert resp.status_code == 202

        body = resp.json()
        assert "job_id" in body["data"]
        assert body["data"]["url"] == "https://example.com"
        assert body["data"]["status"] == "pending"


def test_crawl_invalid_url(client):
    resp = client.post("/crawl", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_crawl_missing_url(client):
    resp = client.post("/crawl", json={})
    assert resp.status_code == 422


def test_crawl_response_has_standard_envelope(client):
    with patch("api.routes.crawl.get_redis_client") as mock_redis, \
         patch("api.routes.crawl.get_pg_connection") as mock_pg:
        mock_rdb = MagicMock()
        mock_redis.return_value = mock_rdb

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("test-job-id",)
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.post("/crawl", json={"url": "https://example.com"})
        body = resp.json()
        assert "data" in body
        assert "sources" in body
        assert "confidence" in body
        assert "freshness" in body
