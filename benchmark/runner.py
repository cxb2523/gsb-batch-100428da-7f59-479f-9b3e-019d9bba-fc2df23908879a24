"""Benchmark runner — orchestrates adapters, collects results, computes metrics."""

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

from benchmark.adapters.base import BaseAdapter, CrawlResult
from benchmark.adapters.phantombot_adapter import PhantombotAdapter
from benchmark.adapters.crawl4ai_adapter import Crawl4AIAdapter
from benchmark.adapters.firecrawl_adapter import FirecrawlAdapter
from benchmark.metrics.speed import compute_speed_metrics
from benchmark.metrics.extraction import compute_extraction_metrics
from benchmark.metrics.schema import compute_schema_metrics
from benchmark.metrics.reliability import compute_reliability_metrics

ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {
    "phantombot": PhantombotAdapter,
    "crawl4ai": Crawl4AIAdapter,
    "firecrawl": FirecrawlAdapter,
}

BENCHMARK_DIR = pathlib.Path(__file__).parent


def load_corpus(corpus_path: str) -> dict:
    """Load and return the corpus JSON manifest."""
    path = pathlib.Path(corpus_path)
    if not path.exists():
        raise FileNotFoundError(f"Corpus file not found: {corpus_path}")
    with open(path) as f:
        return json.load(f)


def load_ground_truth(ground_truth_dir: str, url_id: str) -> dict:
    """Load entity and schema ground truth for a URL ID."""
    gt_dir = pathlib.Path(ground_truth_dir)
    entities = []
    schemas_gt = {"schemas": []}

    entity_file = gt_dir / f"{url_id}-entities.json"
    if entity_file.exists():
        with open(entity_file) as f:
            data = json.load(f)
            entities = data.get("entities", [])

    schema_file = gt_dir / f"{url_id}-schemas.json"
    if schema_file.exists():
        with open(schema_file) as f:
            schemas_gt = json.load(f)

    return {"entities": entities, "schemas": schemas_gt}


def run_adapter_benchmark(
    adapter: BaseAdapter,
    corpus_entries: list[dict],
    timeout_per_url: int = 30,
) -> dict:
    """Run the benchmark for a single adapter against the corpus.

    Returns a dict with keys:
        - tool_name, tool_version
        - crawl_results: list of CrawlResult
        - entity_results: list of list[dict] (per-URL entities)
        - schema_results: list of list[dict] (per-URL schemas)
    """
    adapter.setup()

    crawl_results: list[CrawlResult] = []
    entity_results: list[list[dict]] = []
    schema_results: list[list[dict]] = []

    for entry in corpus_entries:
        url = entry["url"]
        print(f"  [{adapter.name()}] Crawling {url}...")

        crawl_result = adapter.crawl(url, timeout=timeout_per_url)
        crawl_results.append(crawl_result)

        if crawl_result.success and crawl_result.html:
            entities = adapter.extract_entities(crawl_result.html, url)
            schemas = adapter.extract_schemas(crawl_result.html, url)
        else:
            entities = []
            schemas = []

        entity_results.append(entities)
        schema_results.append(schemas)

    adapter.teardown()

    return {
        "tool_name": adapter.name(),
        "tool_version": adapter.version(),
        "crawl_results": crawl_results,
        "entity_results": entity_results,
        "schema_results": schema_results,
    }


def compute_all_metrics(
    adapter_results: dict,
    corpus_entries: list[dict],
    ground_truth_dir: str,
) -> dict:
    """Compute all metrics for a single adapter's results."""
    crawl_results = adapter_results["crawl_results"]
    entity_results = adapter_results["entity_results"]
    schema_results = adapter_results["schema_results"]

    # Speed metrics
    speed = compute_speed_metrics(crawl_results)

    # Extraction metrics (aggregated across URLs with ground truth)
    all_extraction_metrics = []
    for i, entry in enumerate(corpus_entries):
        gt = load_ground_truth(ground_truth_dir, entry["id"])
        if gt["entities"]:
            extraction = compute_extraction_metrics(entity_results[i], gt["entities"])
            all_extraction_metrics.append(extraction)

    # Average extraction metrics
    if all_extraction_metrics:
        avg_extraction = {
            "precision": sum(m["precision"] for m in all_extraction_metrics) / len(all_extraction_metrics),
            "recall": sum(m["recall"] for m in all_extraction_metrics) / len(all_extraction_metrics),
            "f1": sum(m["f1"] for m in all_extraction_metrics) / len(all_extraction_metrics),
            "type_accuracy": sum(m["type_accuracy"] for m in all_extraction_metrics) / len(all_extraction_metrics),
            "urls_evaluated": len(all_extraction_metrics),
        }
    else:
        avg_extraction = {
            "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "type_accuracy": 0.0, "urls_evaluated": 0,
        }

    # Schema metrics (aggregated across URLs with ground truth)
    all_schema_metrics = []
    for i, entry in enumerate(corpus_entries):
        gt = load_ground_truth(ground_truth_dir, entry["id"])
        if gt["schemas"].get("schemas"):
            schema_m = compute_schema_metrics(schema_results[i], gt["schemas"])
            all_schema_metrics.append(schema_m)

    if all_schema_metrics:
        avg_schema = {
            "jsonld_detection_rate": sum(m["jsonld_detection_rate"] for m in all_schema_metrics) / len(all_schema_metrics),
            "openapi_detection_rate": sum(m["openapi_detection_rate"] for m in all_schema_metrics) / len(all_schema_metrics),
            "schema_completeness": sum(m["schema_completeness"] for m in all_schema_metrics) / len(all_schema_metrics),
            "false_positive_rate": sum(m["false_positive_rate"] for m in all_schema_metrics) / len(all_schema_metrics),
            "urls_evaluated": len(all_schema_metrics),
        }
    else:
        avg_schema = {
            "jsonld_detection_rate": 0.0, "openapi_detection_rate": 0.0,
            "schema_completeness": 0.0, "false_positive_rate": 0.0,
            "urls_evaluated": 0,
        }

    # Reliability metrics
    reliability = compute_reliability_metrics(crawl_results, corpus_entries)

    return {
        "speed": speed,
        "extraction": avg_extraction,
        "schemas": avg_schema,
        "reliability": reliability,
    }


def run_benchmark(
    tools: list[str],
    corpus_path: str,
    ground_truth_dir: str,
    output_dir: str,
    timeout_per_url: int = 30,
) -> dict:
    """Run the full benchmark suite.

    Args:
        tools: List of tool names to benchmark.
        corpus_path: Path to corpus.json.
        ground_truth_dir: Path to ground_truth/ directory.
        output_dir: Path to write results.
        timeout_per_url: Timeout in seconds per URL.

    Returns the full results dict.
    """
    corpus = load_corpus(corpus_path)
    corpus_entries = corpus["urls"]

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    results = {
        "metadata": {
            "timestamp": timestamp,
            "corpus_version": corpus.get("version", "unknown"),
            "tools": tools,
            "timeout_per_url": timeout_per_url,
            "urls_count": len(corpus_entries),
        },
        "results": {},
    }

    for tool_name in tools:
        if tool_name not in ADAPTER_REGISTRY:
            print(f"Warning: unknown tool '{tool_name}', skipping.")
            continue

        print(f"\n{'='*60}")
        print(f"Running benchmark for: {tool_name}")
        print(f"{'='*60}")

        adapter = ADAPTER_REGISTRY[tool_name]()
        adapter_results = run_adapter_benchmark(adapter, corpus_entries, timeout_per_url)
        metrics = compute_all_metrics(adapter_results, corpus_entries, ground_truth_dir)

        results["results"][tool_name] = {
            "version": adapter_results["tool_version"],
            **metrics,
            "per_url": [
                {
                    "url": entry["url"],
                    "id": entry["id"],
                    "category": entry["category"],
                    "crawl_success": adapter_results["crawl_results"][i].success,
                    "crawl_time": adapter_results["crawl_results"][i].elapsed_seconds,
                    "crawl_error": adapter_results["crawl_results"][i].error,
                    "entities_found": len(adapter_results["entity_results"][i]),
                    "schemas_found": len(adapter_results["schema_results"][i]),
                }
                for i, entry in enumerate(corpus_entries)
            ],
        }

        # Cool-down between tools
        if tool_name != tools[-1]:
            print(f"\nCooling down for 10 seconds...")
            time.sleep(10)

    # Save results
    output_path = pathlib.Path(output_dir) / timestamp
    output_path.mkdir(parents=True, exist_ok=True)
    results_file = output_path / "raw_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {results_file}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Phantombot Benchmark Suite")
    parser.add_argument(
        "--tools",
        type=str,
        default="phantombot,crawl4ai,firecrawl",
        help="Comma-separated list of tools to benchmark (default: all three)",
    )
    parser.add_argument(
        "--corpus",
        type=str,
        default=str(BENCHMARK_DIR / "corpus.json"),
        help="Path to corpus.json",
    )
    parser.add_argument(
        "--ground-truth",
        type=str,
        default=str(BENCHMARK_DIR / "ground_truth"),
        help="Path to ground_truth/ directory",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(BENCHMARK_DIR / "results"),
        help="Path to output directory",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout per URL in seconds (default: 30)",
    )
    parser.add_argument(
        "--use-archive",
        action="store_true",
        help="Use cached/archived pages instead of live crawling (v2 feature)",
    )

    args = parser.parse_args()
    tools = [t.strip() for t in args.tools.split(",")]

    results = run_benchmark(
        tools=tools,
        corpus_path=args.corpus,
        ground_truth_dir=args.ground_truth,
        output_dir=args.output,
        timeout_per_url=args.timeout,
    )

    # Generate report
    from benchmark.report import generate_markdown_report
    timestamp = results["metadata"]["timestamp"]
    output_path = pathlib.Path(args.output) / timestamp
    report_path = output_path / "report.md"
    report = generate_markdown_report(results)
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report saved to {report_path}")


if __name__ == "__main__":
    main()
