import pytest
from unittest.mock import patch, MagicMock
from benchmark.adapters.firecrawl_adapter import FirecrawlAdapter
from benchmark.adapters.base import CrawlResult


@pytest.fixture
def adapter():
    return FirecrawlAdapter()


class TestFirecrawlAdapterMetadata:
    def test_name(self, adapter):
        assert adapter.name() == "firecrawl"

    def test_version(self, adapter):
        version = adapter.version()
        assert isinstance(version, str)
        assert len(version) > 0


class TestFirecrawlEntityExtraction:
    def test_extracts_entities_from_entity_rich_html(self, adapter, entity_rich_html):
        entities = adapter.extract_entities(entity_rich_html, "https://example.com/news")
        assert isinstance(entities, list)
        assert len(entities) > 0
        texts = [e["text"] for e in entities]
        assert any("Google" in t for t in texts)

    def test_entity_format(self, adapter, entity_rich_html):
        entities = adapter.extract_entities(entity_rich_html, "https://example.com/news")
        for entity in entities:
            assert "text" in entity
            assert "type" in entity

    def test_returns_empty_for_empty_html(self, adapter):
        entities = adapter.extract_entities("", "https://example.com/empty")
        assert entities == []


class TestFirecrawlSchemaExtraction:
    def test_extracts_jsonld(self, adapter, jsonld_html):
        schemas = adapter.extract_schemas(jsonld_html, "https://example.com/jsonld")
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert schemas[0]["type"] == "JSON_LD"

    def test_returns_empty_for_no_schemas(self, adapter, simple_html):
        schemas = adapter.extract_schemas(simple_html, "https://example.com/simple")
        assert schemas == []


class TestFirecrawlCrawl:
    @patch("benchmark.adapters.firecrawl_adapter.requests.post")
    def test_crawl_returns_crawl_result(self, mock_post, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "html": "<html><body><p>Hello World</p></body></html>",
                "markdown": "Hello World",
                "rawHtml": "<html><body><p>Hello World</p></body></html>",
                "metadata": {"statusCode": 200},
            },
        }
        mock_response.elapsed.total_seconds.return_value = 0.5
        mock_post.return_value = mock_response

        result = adapter.crawl("https://example.com", timeout=30)
        assert isinstance(result, CrawlResult)
        assert result.success is True
        assert result.url == "https://example.com"

    @patch("benchmark.adapters.firecrawl_adapter.requests.post")
    def test_crawl_handles_api_failure(self, mock_post, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False,
            "error": "Failed to scrape",
        }
        mock_response.elapsed.total_seconds.return_value = 1.0
        mock_post.return_value = mock_response

        result = adapter.crawl("https://example.com/bad", timeout=30)
        assert result.success is False
        assert result.error is not None

    @patch("benchmark.adapters.firecrawl_adapter.requests.post")
    def test_crawl_handles_timeout(self, mock_post, adapter):
        import requests
        mock_post.side_effect = requests.Timeout("Connection timed out")

        result = adapter.crawl("https://example.com/slow", timeout=1)
        assert result.success is False
        assert result.error is not None
