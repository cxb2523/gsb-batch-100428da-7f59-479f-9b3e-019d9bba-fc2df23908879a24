import pytest
from benchmark.config import BenchmarkConfig


class TestBenchmarkConfig:
    def test_default_values(self):
        config = BenchmarkConfig()
        assert config.timeout_per_url == 30
        assert config.total_timeout == 900
        assert config.concurrency == 1
        assert config.use_archive is False
        assert config.spacy_model == "en_core_web_sm"

    def test_default_tools(self):
        config = BenchmarkConfig()
        assert "phantombot" in config.tools
        assert "firecrawl" in config.tools
        assert "crawl4ai" in config.tools

    def test_custom_values(self):
        config = BenchmarkConfig(
            timeout_per_url=60,
            tools=["phantombot"],
            use_archive=True,
        )
        assert config.timeout_per_url == 60
        assert config.tools == ["phantombot"]
        assert config.use_archive is True

    def test_paths_are_strings(self):
        config = BenchmarkConfig()
        assert isinstance(config.corpus_path, str)
        assert isinstance(config.ground_truth_dir, str)
        assert isinstance(config.output_dir, str)

    def test_corpus_path_default(self):
        config = BenchmarkConfig()
        assert "corpus.json" in config.corpus_path

    def test_ground_truth_dir_default(self):
        config = BenchmarkConfig()
        assert "ground_truth" in config.ground_truth_dir
