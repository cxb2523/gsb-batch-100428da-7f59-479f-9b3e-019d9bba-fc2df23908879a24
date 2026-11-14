from unittest.mock import MagicMock, patch

import pytest

from mcp_server.server import search_semantic


class MockPoint:
    def __init__(self, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class MockQueryResponse:
    def __init__(self, points):
        self.points = points


class TestSearchSemantic:
    """Tests for the search_semantic MCP tool."""

    @patch("mcp_server.server.db")
    def test_returns_results(self, mock_db):
        mock_embedder = MagicMock()
        mock_embedder.encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
        mock_db.get_embedder.return_value = mock_embedder

        mock_qdrant = MagicMock()
        mock_qdrant.query_points.return_value = MockQueryResponse([
            MockPoint(
                id="abc-123",
                score=0.95,
                payload={
                    "type": "entity",
                    "page_id": "page-1",
                    "entity_type": "ORG",
                    "entity_text": "Google",
                    "confidence": 0.9,
                },
            )
        ])
        mock_db.get_qdrant_client.return_value = mock_qdrant

        result = search_semantic(query="large tech companies", limit=10)

        assert "results" in result
        assert "total" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["entity_text"] == "Google"
        assert result["results"][0]["score"] == 0.95

    @patch("mcp_server.server.db")
    def test_empty_results(self, mock_db):
        mock_embedder = MagicMock()
        mock_embedder.encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
        mock_db.get_embedder.return_value = mock_embedder

        mock_qdrant = MagicMock()
        mock_qdrant.query_points.return_value = MockQueryResponse([])
        mock_db.get_qdrant_client.return_value = mock_qdrant

        result = search_semantic(query="nonexistent stuff", limit=10)

        assert result["results"] == []
        assert result["total"] == 0

    @patch("mcp_server.server.db")
    def test_limit_clamped(self, mock_db):
        mock_embedder = MagicMock()
        mock_embedder.encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
        mock_db.get_embedder.return_value = mock_embedder

        mock_qdrant = MagicMock()
        mock_qdrant.query_points.return_value = MockQueryResponse([])
        mock_db.get_qdrant_client.return_value = mock_qdrant

        result = search_semantic(query="test", limit=999)
        assert isinstance(result, dict)
        # Verify qdrant was called with clamped limit
        call_args = mock_qdrant.query_points.call_args
        assert call_args.kwargs.get("limit", call_args[1].get("limit", 50)) <= 50
