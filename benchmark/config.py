import pathlib
from dataclasses import dataclass, field

_BENCHMARK_DIR = pathlib.Path(__file__).parent


@dataclass
class BenchmarkConfig:
    """Configuration for the benchmark suite."""

    corpus_path: str = str(_BENCHMARK_DIR / "corpus.json")
    ground_truth_dir: str = str(_BENCHMARK_DIR / "ground_truth")
    output_dir: str = str(_BENCHMARK_DIR / "results")
    timeout_per_url: int = 30
    total_timeout: int = 900
    concurrency: int = 1
    use_archive: bool = False
    tools: list[str] = field(default_factory=lambda: ["phantombot", "firecrawl", "crawl4ai"])
    spacy_model: str = "en_core_web_sm"
