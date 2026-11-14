import json
import pathlib
import pytest
from unittest.mock import MagicMock

from benchmark.adapters.base import CrawlResult
from benchmark.runner import run_adapter_benchmark, compute_all_metrics, load_corpus
from benchmark.report import generate_markdown_report

FIXTURES_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures"
GROUND_TRUTH_DIR = pathlib.Path(__file__).parent.parent / "ground_truth"
CORPUS_PATH = pathlib.Path(__file__).parent.parent / "corpus.json"


class TestFullPipelineIntegration:
    def test_mock_adapter_through_full_pipeline(self):
        """Run a mock adapter through the full pipeline: crawl -> extract -> metrics -> report."""
        mock_adapter = MagicMock()
        mock_adapter.name.return_value = "mock_tool"
        mock_adapter.version.return_value = "0.0.1"

        html = (FIXTURES_DIR / "entity_rich_page.html").read_text()
        mock_adapter.crawl.return_value = CrawlResult(
            url="https://example.com", status_code=200,
            html=html, markdown="Tech news", text="Tech news",
            metadata={}, elapsed_seconds=0.5, success=True,
        )
        mock_adapter.extract_entities.return_value = [
            {"text": "Google", "type": "ORG"},
            {"text": "Sundar Pichai", "type": "PERSON"},
        ]
        mock_adapter.extract_schemas.return_value = []

        corpus_entries = [
            {"id": "static-001", "url": "https://example.com", "category": "static_html",
             "expected_entities": 5, "expected_schemas": 0, "notes": "test"},
        ]

        # Run adapter benchmark
        results = run_adapter_benchmark(mock_adapter, corpus_entries, timeout_per_url=30)
        assert results["tool_name"] == "mock_tool"
        assert len(results["crawl_results"]) == 1
        assert results["crawl_results"][0].success is True

        # Compute metrics
        metrics = compute_all_metrics(results, corpus_entries, str(GROUND_TRUTH_DIR))
        assert "speed" in metrics
        assert "extraction" in metrics
        assert "schemas" in metrics
        assert "reliability" in metrics

        # Generate report
        full_results = {
            "metadata": {
                "timestamp": "2026-04-12T10:00:00",
                "corpus_version": "1.0",
                "tools": ["mock_tool"],
                "timeout_per_url": 30,
                "urls_count": 1,
            },
            "results": {
                "mock_tool": {
                    "version": "0.0.1",
                    **metrics,
                    "per_url": [],
                },
            },
        }
        report = generate_markdown_report(full_results)
        assert "# Phantombot Benchmark Results" in report
        assert "mock_tool" in report

    def test_corpus_loads_successfully(self):
        corpus = load_corpus(str(CORPUS_PATH))
        assert corpus["version"] == "1.0"
        assert len(corpus["urls"]) == 25

    def test_ground_truth_files_are_valid(self):
        entity_files = list(GROUND_TRUTH_DIR.glob("*-entities.json"))
        assert len(entity_files) >= 10
        for path in entity_files:
            with open(path) as f:
                data = json.load(f)
            assert "entities" in data
            assert len(data["entities"]) > 0
