import json
import pathlib
import pytest
from unittest.mock import MagicMock, patch

from benchmark.adapters.base import CrawlResult
from benchmark.runner import load_corpus, run_adapter_benchmark, ADAPTER_REGISTRY


class TestLoadCorpus:
    def test_loads_corpus_from_path(self):
        corpus_path = pathlib.Path(__file__).parent.parent / "corpus.json"
        corpus = load_corpus(str(corpus_path))
        assert "urls" in corpus
        assert len(corpus["urls"]) == 25

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_corpus("/nonexistent/corpus.json")


class TestAdapterRegistry:
    def test_registry_has_all_three_tools(self):
        assert "phantombot" in ADAPTER_REGISTRY
        assert "crawl4ai" in ADAPTER_REGISTRY
        assert "firecrawl" in ADAPTER_REGISTRY


class TestRunAdapterBenchmark:
    def test_runs_adapter_against_urls(self):
        mock_adapter = MagicMock()
        mock_adapter.name.return_value = "test_tool"
        mock_adapter.version.return_value = "1.0.0"
        mock_adapter.crawl.return_value = CrawlResult(
            url="https://example.com", status_code=200,
            html="<p>hello</p>", markdown="hello", text="hello",
            metadata={}, elapsed_seconds=0.5, success=True,
        )
        mock_adapter.extract_entities.return_value = [{"text": "Example", "type": "ORG"}]
        mock_adapter.extract_schemas.return_value = []

        corpus_entries = [
            {"id": "test-001", "url": "https://example.com", "category": "static_html",
             "expected_entities": 1, "expected_schemas": 0, "notes": "test"},
        ]

        results = run_adapter_benchmark(mock_adapter, corpus_entries, timeout_per_url=30)

        assert len(results["crawl_results"]) == 1
        assert len(results["entity_results"]) == 1
        assert len(results["schema_results"]) == 1
        assert results["tool_name"] == "test_tool"
        assert results["tool_version"] == "1.0.0"
        mock_adapter.setup.assert_called_once()
        mock_adapter.teardown.assert_called_once()

    def test_handles_crawl_failure(self):
        mock_adapter = MagicMock()
        mock_adapter.name.return_value = "test_tool"
        mock_adapter.version.return_value = "1.0.0"
        mock_adapter.crawl.return_value = CrawlResult(
            url="https://bad.com", status_code=0,
            html="", markdown="", text="",
            metadata={}, elapsed_seconds=30.0, success=False, error="Timeout",
        )
        mock_adapter.extract_entities.return_value = []
        mock_adapter.extract_schemas.return_value = []

        corpus_entries = [
            {"id": "test-001", "url": "https://bad.com", "category": "static_html",
             "expected_entities": 1, "expected_schemas": 0, "notes": "test"},
        ]

        results = run_adapter_benchmark(mock_adapter, corpus_entries, timeout_per_url=30)
        assert results["crawl_results"][0].success is False
