import pytest
from benchmark.adapters.base import CrawlResult
from benchmark.metrics.speed import compute_speed_metrics


def _make_result(url: str, elapsed: float, success: bool = True) -> CrawlResult:
    return CrawlResult(
        url=url, status_code=200 if success else 0, html="<p>ok</p>",
        markdown="ok", text="ok", metadata={}, elapsed_seconds=elapsed,
        success=success, error=None if success else "failed",
    )


class TestComputeSpeedMetrics:
    def test_basic_speed_metrics(self):
        results = [
            _make_result("https://a.com", 1.0),
            _make_result("https://b.com", 2.0),
            _make_result("https://c.com", 3.0),
        ]
        metrics = compute_speed_metrics(results)
        assert metrics["time_to_first_result"] == 1.0
        assert metrics["total_corpus_time"] == pytest.approx(6.0)
        assert metrics["pages_per_second"] == pytest.approx(3.0 / 6.0)
        assert metrics["urls_attempted"] == 3

    def test_with_failures(self):
        results = [
            _make_result("https://a.com", 1.0, success=True),
            _make_result("https://b.com", 30.0, success=False),
            _make_result("https://c.com", 2.0, success=True),
        ]
        metrics = compute_speed_metrics(results)
        assert metrics["time_to_first_result"] == 1.0
        assert metrics["total_corpus_time"] == pytest.approx(33.0)
        assert metrics["urls_succeeded"] == 2
        assert metrics["urls_attempted"] == 3

    def test_all_failures(self):
        results = [
            _make_result("https://a.com", 30.0, success=False),
            _make_result("https://b.com", 30.0, success=False),
        ]
        metrics = compute_speed_metrics(results)
        assert metrics["time_to_first_result"] is None
        assert metrics["pages_per_second"] == 0.0
        assert metrics["urls_succeeded"] == 0

    def test_empty_results(self):
        metrics = compute_speed_metrics([])
        assert metrics["time_to_first_result"] is None
        assert metrics["total_corpus_time"] == 0.0
        assert metrics["pages_per_second"] == 0.0
        assert metrics["urls_attempted"] == 0

    def test_single_result(self):
        results = [_make_result("https://a.com", 2.5)]
        metrics = compute_speed_metrics(results)
        assert metrics["time_to_first_result"] == 2.5
        assert metrics["total_corpus_time"] == 2.5
        assert metrics["pages_per_second"] == pytest.approx(1.0 / 2.5)
