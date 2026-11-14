import pytest
from unittest.mock import patch, MagicMock
from benchmark.adapters.phantombot_adapter import PhantombotAdapter
from benchmark.adapters.base import CrawlResult


@pytest.fixture
def adapter():
    return PhantombotAdapter()


class TestPhantombotAdapterMetadata:
    def test_name(self, adapter):
        assert adapter.name() == "phantombot"

    def test_version(self, adapter):
        version = adapter.version()
        assert isinstance(version, str)
        assert len(version) > 0


class TestPhantombotEntityExtraction:
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
            assert isinstance(entity["text"], str)
            assert entity["type"] in ("PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "LOC", "FAC", "NORP", "EVENT")

    def test_returns_empty_for_empty_html(self, adapter):
        entities = adapter.extract_entities("", "https://example.com/empty")
        assert entities == []


class TestPhantombotSchemaExtraction:
    def test_extracts_jsonld(self, adapter, jsonld_html):
        schemas = adapter.extract_schemas(jsonld_html, "https://example.com/jsonld")
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert schemas[0]["type"] == "JSON_LD"

    def test_extracts_openapi(self, adapter, openapi_html):
        schemas = adapter.extract_schemas(openapi_html, "https://example.com/api")
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert schemas[0]["type"] == "OPENAPI"

    def test_schema_format(self, adapter, jsonld_html):
        schemas = adapter.extract_schemas(jsonld_html, "https://example.com/jsonld")
        for schema in schemas:
            assert "type" in schema
            assert "data" in schema
            assert schema["type"] in ("JSON_LD", "OPENAPI")

    def test_returns_empty_for_no_schemas(self, adapter, simple_html):
        schemas = adapter.extract_schemas(simple_html, "https://example.com/simple")
        assert schemas == []


class TestPhantombotCrawl:
    @patch("benchmark.adapters.phantombot_adapter.requests.get")
    def test_crawl_returns_crawl_result(self, mock_get, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.elapsed.total_seconds.return_value = 0.5
        mock_get.return_value = mock_response

        result = adapter.crawl("https://example.com", timeout=30)
        assert isinstance(result, CrawlResult)
        assert result.success is True
        assert result.status_code == 200
        assert result.url == "https://example.com"

    @patch("benchmark.adapters.phantombot_adapter.requests.get")
    def test_crawl_handles_timeout(self, mock_get, adapter):
        import requests
        mock_get.side_effect = requests.Timeout("Connection timed out")

        result = adapter.crawl("https://example.com/slow", timeout=1)
        assert result.success is False
        assert result.error is not None
        assert "timed out" in result.error.lower() or "timeout" in result.error.lower()
