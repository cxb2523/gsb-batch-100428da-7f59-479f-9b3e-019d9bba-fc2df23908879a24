from unittest.mock import patch, MagicMock
import pytest


def test_scrape_valid_url(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body><h1>Test</h1><p>Hello world</p></body></html>"

    with patch("api.routes.scrape.requests.get", return_value=mock_response):
        resp = client.post("/scrape", json={"url": "https://example.com"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["url"] == "https://example.com"
        assert body["data"]["status_code"] == 200
        assert "fetch_time_seconds" in body["data"]


def test_scrape_invalid_url(client):
    resp = client.post("/scrape", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_scrape_timeout(client):
    import requests as req
    with patch("api.routes.scrape.requests.get", side_effect=req.Timeout("timeout")):
        resp = client.post("/scrape", json={"url": "https://slow.example.com"})
        assert resp.status_code == 504


def test_scrape_response_envelope(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body>Test</body></html>"

    with patch("api.routes.scrape.requests.get", return_value=mock_response):
        resp = client.post("/scrape", json={"url": "https://example.com"})
        body = resp.json()
        assert "data" in body
        assert "sources" in body
        assert "confidence" in body
