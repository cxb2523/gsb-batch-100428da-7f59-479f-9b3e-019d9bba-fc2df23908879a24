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


def test_render_response_reports_response_metadata():
    """RenderResponse carries the content type/headers the browser saw."""
    from server import RenderResponse
    resp = RenderResponse(
        url="https://example.com",
        html="<html></html>",
        status_code=200,
        title="Example",
        content_type="text/html; charset=utf-8",
        headers={"content-type": "text/html; charset=utf-8"},
    )
    assert resp.content_type == "text/html; charset=utf-8"
    assert resp.headers["content-type"] == "text/html; charset=utf-8"


def test_render_response_metadata_is_optional():
    """Older callers/tests can still construct a response without metadata."""
    from server import RenderResponse
    resp = RenderResponse(url="https://example.com", html="<html></html>", status_code=200, title="")
    assert resp.content_type == ""
    assert resp.headers == {}


def test_render_timeout_returns_504():
    """Playwright timeouts are reported as 504 so the crawler can classify them."""
    from server import app

    class TimeoutError_(Exception):
        pass
    TimeoutError_.__name__ = "TimeoutError"

    page = MagicMock()
    page.goto = AsyncMock(side_effect=TimeoutError_("Timeout 15000ms exceeded"))
    page.close = AsyncMock()
    fake_browser = MagicMock()
    fake_browser.new_page = AsyncMock(return_value=page)

    with patch("server.browser", fake_browser):
        client = TestClient(app)
        resp = client.post("/render", json={"url": "https://example.com"})
        assert resp.status_code == 504
        assert "Render failed" in resp.json()["detail"]
