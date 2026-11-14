import pytest
from benchmark.adapters.base import CrawlResult, ExtractionResult, BaseAdapter


class TestCrawlResult:
    def test_create_successful_result(self):
        result = CrawlResult(
            url="https://example.com",
            status_code=200,
            html="<html><body>Hello</body></html>",
            markdown="Hello",
            text="Hello",
            metadata={},
            elapsed_seconds=1.5,
            success=True,
        )
        assert result.url == "https://example.com"
        assert result.status_code == 200
        assert result.success is True
        assert result.error is None
        assert result.elapsed_seconds == 1.5

    def test_create_failed_result(self):
        result = CrawlResult(
            url="https://example.com/bad",
            status_code=500,
            html="",
            markdown="",
            text="",
            metadata={},
            elapsed_seconds=30.0,
            success=False,
            error="Timeout exceeded",
        )
        assert result.success is False
        assert result.error == "Timeout exceeded"

    def test_default_error_is_none(self):
        result = CrawlResult(
            url="https://example.com",
            status_code=200,
            html="<p>Hi</p>",
            markdown="Hi",
            text="Hi",
            metadata={"tool": "test"},
            elapsed_seconds=0.1,
            success=True,
        )
        assert result.error is None


class TestExtractionResult:
    def test_create_with_entities_and_schemas(self):
        result = ExtractionResult(
            entities=[
                {"text": "Google", "type": "ORG"},
                {"text": "Sundar Pichai", "type": "PERSON"},
            ],
            schemas=[
                {"type": "JSON_LD", "data": {"@type": "Organization"}},
            ],
        )
        assert len(result.entities) == 2
        assert len(result.schemas) == 1

    def test_create_empty(self):
        result = ExtractionResult(entities=[], schemas=[])
        assert result.entities == []
        assert result.schemas == []


class TestBaseAdapterInterface:
    def test_cannot_instantiate_base_adapter(self):
        with pytest.raises(TypeError):
            BaseAdapter()

    def test_concrete_adapter_must_implement_all_methods(self):
        class IncompleteAdapter(BaseAdapter):
            pass

        with pytest.raises(TypeError):
            IncompleteAdapter()

    def test_concrete_adapter_with_all_methods(self):
        class CompleteAdapter(BaseAdapter):
            def name(self) -> str:
                return "test"

            def version(self) -> str:
                return "1.0.0"

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            def crawl(self, url: str, timeout: int = 30) -> CrawlResult:
                return CrawlResult(
                    url=url,
                    status_code=200,
                    html="<p>test</p>",
                    markdown="test",
                    text="test",
                    metadata={},
                    elapsed_seconds=0.1,
                    success=True,
                )

            def extract_entities(self, html: str, url: str) -> list[dict]:
                return []

            def extract_schemas(self, html: str, url: str) -> list[dict]:
                return []

        adapter = CompleteAdapter()
        assert adapter.name() == "test"
        assert adapter.version() == "1.0.0"
