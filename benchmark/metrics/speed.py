from benchmark.adapters.base import CrawlResult


def compute_speed_metrics(results: list[CrawlResult]) -> dict:
    """Compute crawl speed metrics from a list of CrawlResults.

    Returns a dict with keys:
        - time_to_first_result: seconds to first successful result (None if all failed)
        - total_corpus_time: total wall-clock seconds across all URLs
        - pages_per_second: successful pages / total_corpus_time
        - urls_attempted: total URLs attempted
        - urls_succeeded: total URLs that returned usable content
    """
    if not results:
        return {
            "time_to_first_result": None,
            "total_corpus_time": 0.0,
            "pages_per_second": 0.0,
            "urls_attempted": 0,
            "urls_succeeded": 0,
        }

    total_time = sum(r.elapsed_seconds for r in results)
    succeeded = [r for r in results if r.success]

    # Time to first successful result (in order of the list)
    time_to_first = None
    cumulative = 0.0
    for r in results:
        cumulative += r.elapsed_seconds
        if r.success:
            time_to_first = cumulative
            break

    pages_per_second = len(succeeded) / total_time if total_time > 0 else 0.0

    return {
        "time_to_first_result": time_to_first,
        "total_corpus_time": total_time,
        "pages_per_second": pages_per_second,
        "urls_attempted": len(results),
        "urls_succeeded": len(succeeded),
    }
