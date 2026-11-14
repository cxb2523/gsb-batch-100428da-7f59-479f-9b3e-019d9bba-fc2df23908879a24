import pytest
from benchmark.adapters.base import CrawlResult
from benchmark.metrics.reliability import compute_reliability_metrics


def _make_result(url: str, success: bool, category: str = "static_html") -> CrawlResult:
    return CrawlResult(
        url=url, status_code=200 if success else 0, html="<p>ok</p>" if success else "",
        markdown="ok" if success else "", text="ok" if success else "",
        metadata={"category": category}, elapsed_seconds=1.0,
        success=success, error=None if success else "Failed",
    )


class TestComputeReliabilityMetrics:
    def test_all_success(self):
        results = [
            _make_result("https://a.com", True, "static_html"),
            _make_result("https://b.com", True, "static_html"),
            _make_result("https://c.com", True, "js_heavy_spa"),
        ]
        corpus_entries = [
            {"id": "s1", "url": "https://a.com", "category": "static_html"},
            {"id": "s2", "url": "https://b.com", "category": "static_html"},
            {"id": "j1", "url": "https://c.com", "category": "js_heavy_spa"},
        ]
        metrics = compute_reliability_metrics(results, corpus_entries)
        assert metrics["success_rate"] == pytest.approx(1.0)
        assert metrics["error_rate_by_category"]["static_html"] == pytest.approx(0.0)
        assert metrics["error_rate_by_category"]["js_heavy_spa"] == pytest.approx(0.0)

    def test_partial_failure(self):
        results = [
            _make_result("https://a.com", True, "static_html"),
            _make_result("https://b.com", False, "static_html"),
            _make_result("https://c.com", False, "js_heavy_spa"),
            _make_result("https://d.com", True, "js_heavy_spa"),
        ]
        corpus_entries = [
            {"id": "s1", "url": "https://a.com", "category": "static_html"},
            {"id": "s2", "url": "https://b.com", "category": "static_html"},
            {"id": "j1", "url": "https://c.com", "category": "js_heavy_spa"},
            {"id": "j2", "url": "https://d.com", "category": "js_heavy_spa"},
        ]
        metrics = compute_reliability_metrics(results, corpus_entries)
        assert metrics["success_rate"] == pytest.approx(0.5)
        assert metrics["error_rate_by_category"]["static_html"] == pytest.approx(0.5)
        assert metrics["error_rate_by_category"]["js_heavy_spa"] == pytest.approx(0.5)

    def test_all_failure(self):
        results = [
            _make_result("https://a.com", False, "static_html"),
            _make_result("https://b.com", False, "static_html"),
        ]
        corpus_entries = [
            {"id": "s1", "url": "https://a.com", "category": "static_html"},
            {"id": "s2", "url": "https://b.com", "category": "static_html"},
        ]
        metrics = compute_reliability_metrics(results, corpus_entries)
        assert metrics["success_rate"] == pytest.approx(0.0)

    def test_empty_results(self):
        metrics = compute_reliability_metrics([], [])
        assert metrics["success_rate"] == pytest.approx(0.0)
        assert metrics["total_attempted"] == 0
        assert metrics["total_succeeded"] == 0
