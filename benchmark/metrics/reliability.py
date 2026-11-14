from collections import defaultdict

from benchmark.adapters.base import CrawlResult


def compute_reliability_metrics(
    results: list[CrawlResult],
    corpus_entries: list[dict],
) -> dict:
    """Compute reliability metrics from crawl results.

    Args:
        results: List of CrawlResults from a benchmark run.
        corpus_entries: List of corpus entry dicts with "url" and "category" fields.

    Returns dict with keys:
        - success_rate: fraction of URLs returning usable content
        - error_rate_by_category: dict mapping category to error rate
        - total_attempted: total URLs attempted
        - total_succeeded: total successful crawls
        - errors: list of {"url": str, "error": str} for failed URLs
    """
    if not results:
        return {
            "success_rate": 0.0,
            "error_rate_by_category": {},
            "total_attempted": 0,
            "total_succeeded": 0,
            "errors": [],
        }

    # Build URL -> category lookup from corpus entries
    url_to_category = {}
    for entry in corpus_entries:
        url_to_category[entry["url"]] = entry["category"]

    succeeded = sum(1 for r in results if r.success)
    success_rate = succeeded / len(results)

    # Error rate by category
    category_total = defaultdict(int)
    category_errors = defaultdict(int)
    for r in results:
        category = url_to_category.get(r.url, "unknown")
        category_total[category] += 1
        if not r.success:
            category_errors[category] += 1

    error_rate_by_category = {}
    for cat, total in category_total.items():
        error_rate_by_category[cat] = category_errors[cat] / total

    # Collect error details
    errors = []
    for r in results:
        if not r.success:
            errors.append({"url": r.url, "error": r.error or "Unknown error"})

    return {
        "success_rate": success_rate,
        "error_rate_by_category": error_rate_by_category,
        "total_attempted": len(results),
        "total_succeeded": succeeded,
        "errors": errors,
    }
