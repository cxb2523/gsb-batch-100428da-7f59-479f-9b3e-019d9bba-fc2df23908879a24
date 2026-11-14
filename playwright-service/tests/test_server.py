"""Tests for the Playwright render sidecar."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


def test_health_endpoint():
    """Health check returns ok."""
    # Import with browser mocked
    with patch("server.browser", MagicMock()):
        from server import app
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_render_request_model():
    """RenderRequest validates correctly."""
    from server import RenderRequest
    req = RenderRequest(url="https://example.com")
    assert req.url == "https://example.com"
    assert req.wait_until == "networkidle"


def test_render_response_model():
    """RenderResponse validates correctly."""
    from server import RenderResponse
    resp = RenderResponse(url="https://example.com", html="<html></html>", status_code=200, title="Example")
    assert resp.status_code == 200
