import pytest
from benchmark.report import generate_markdown_report


@pytest.fixture
def sample_results():
    return {
        "metadata": {
            "timestamp": "2026-04-12T10:00:00",
            "corpus_version": "1.0",
            "tools": ["phantombot", "crawl4ai"],
            "timeout_per_url": 30,
            "urls_count": 3,
        },
        "results": {
            "phantombot": {
                "version": "0.1.0",
                "speed": {
                    "time_to_first_result": 0.8,
                    "total_corpus_time": 5.0,
                    "pages_per_second": 0.6,
                    "urls_attempted": 3,
                    "urls_succeeded": 3,
                },
                "extraction": {
                    "precision": 0.82,
                    "recall": 0.75,
                    "f1": 0.78,
                    "type_accuracy": 0.91,
                    "urls_evaluated": 2,
                },
                "schemas": {
                    "jsonld_detection_rate": 1.0,
                    "openapi_detection_rate": 0.5,
                    "schema_completeness": 0.85,
                    "false_positive_rate": 0.0,
                    "urls_evaluated": 2,
                },
                "reliability": {
                    "success_rate": 1.0,
                    "error_rate_by_category": {},
                    "total_attempted": 3,
                    "total_succeeded": 3,
                    "errors": [],
                },
                "per_url": [],
            },
            "crawl4ai": {
                "version": "0.4.0",
                "speed": {
                    "time_to_first_result": 0.9,
                    "total_corpus_time": 4.0,
                    "pages_per_second": 0.75,
                    "urls_attempted": 3,
                    "urls_succeeded": 3,
                },
                "extraction": {
                    "precision": 0.80,
                    "recall": 0.73,
                    "f1": 0.76,
                    "type_accuracy": 0.90,
                    "urls_evaluated": 2,
                },
                "schemas": {
                    "jsonld_detection_rate": 0.5,
                    "openapi_detection_rate": 0.0,
                    "schema_completeness": 0.40,
                    "false_positive_rate": 0.1,
                    "urls_evaluated": 2,
                },
                "reliability": {
                    "success_rate": 1.0,
                    "error_rate_by_category": {},
                    "total_attempted": 3,
                    "total_succeeded": 3,
                    "errors": [],
                },
                "per_url": [],
            },
        },
    }


class TestGenerateMarkdownReport:
    def test_returns_string(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert isinstance(report, str)

    def test_contains_title(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "# Phantombot Benchmark Results" in report

    def test_contains_speed_section(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "## Speed" in report

    def test_contains_extraction_section(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "## Entity Extraction Quality" in report

    def test_contains_schema_section(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "## Schema Detection" in report

    def test_contains_reliability_section(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "## Reliability" in report

    def test_contains_tool_names(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "phantombot" in report.lower()
        assert "crawl4ai" in report.lower()

    def test_contains_metric_values(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "0.82" in report  # phantombot precision
        assert "0.78" in report  # phantombot f1

    def test_report_is_valid_markdown(self, sample_results):
        report = generate_markdown_report(sample_results)
        # Basic markdown structure checks
        assert report.startswith("#")
        assert "| Tool |" in report  # has table headers
        assert "|---|" in report     # has table separators
