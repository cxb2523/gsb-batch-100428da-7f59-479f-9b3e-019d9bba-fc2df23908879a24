import pytest
from unittest.mock import patch, MagicMock


def test_search_returns_results(client):
    mock_points = MagicMock()
    mock_points.points = [
        MagicMock(
            id="point-1",
            score=0.92,
            payload={
                "type": "entity",
                "page_id": "page-1",
                "entity_type": "ORG",
                "entity_text": "Google",
                "confidence": 0.9,
            },
        )
    ]

    with patch("api.routes.search.get_qdrant_client") as mock_qdrant, \
         patch("api.routes.search.get_embedder") as mock_embedder:
        mock_embedder.return_value.encode.return_value.tolist.return_value = [0.1] * 384
        mock_qdrant.return_value.query_points.return_value = mock_points

        resp = client.post("/search", json={"query": "AI companies"})
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) >= 1
        assert body["data"][0]["entity_text"] == "Google"
        assert "confidence" in body


def test_search_empty_results(client):
    mock_points = MagicMock()
    mock_points.points = []

    with patch("api.routes.search.get_qdrant_client") as mock_qdrant, \
         patch("api.routes.search.get_embedder") as mock_embedder:
        mock_embedder.return_value.encode.return_value.tolist.return_value = [0.1] * 384
        mock_qdrant.return_value.query_points.return_value = mock_points

        resp = client.post("/search", json={"query": "nothing here"})
        assert resp.status_code == 200
        assert resp.json()["data"] == []


def test_search_missing_query(client):
    resp = client.post("/search", json={})
    assert resp.status_code == 422


def test_search_with_include_markdown(client):
    mock_points = MagicMock()
    mock_points.points = [
        MagicMock(
            id="point-1",
            score=0.92,
            payload={
                "type": "entity",
                "page_id": "page-1",
                "entity_type": "ORG",
                "entity_text": "Google",
                "confidence": 0.9,
            },
        )
    ]

    with patch("api.routes.search.get_qdrant_client") as mock_qdrant, \
         patch("api.routes.search.get_embedder") as mock_embedder, \
         patch("api.routes.search.get_pg_connection") as mock_pg:
        mock_embedder.return_value.encode.return_value.tolist.return_value = [0.1] * 384
        mock_qdrant.return_value.query_points.return_value = mock_points

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("page-1", "# Google\n\nSearch engine.", {"word_count": 20, "pipeline_version": "markdown-v1"})
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pg.return_value = mock_conn

        resp = client.post("/search", json={"query": "AI companies", "include_markdown": True})
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) >= 1
        assert body["data"][0]["markdown"] == "# Google\n\nSearch engine."
