import json
import pathlib

import pytest

CORPUS_PATH = pathlib.Path(__file__).parent.parent / "corpus.json"

REQUIRED_CATEGORIES = {
    "static_html",
    "js_heavy_spa",
    "jsonld_structured",
    "api_documentation",
    "news_entity_rich",
    "ecommerce_product",
}


def load_corpus() -> dict:
    with open(CORPUS_PATH) as f:
        return json.load(f)


class TestCorpusStructure:
    def test_corpus_file_exists(self):
        assert CORPUS_PATH.exists(), f"corpus.json not found at {CORPUS_PATH}"

    def test_corpus_has_version(self):
        corpus = load_corpus()
        assert "version" in corpus
        assert corpus["version"] == "1.0"

    def test_corpus_has_created_at(self):
        corpus = load_corpus()
        assert "created_at" in corpus

    def test_corpus_has_25_urls(self):
        corpus = load_corpus()
        assert len(corpus["urls"]) == 25

    def test_all_urls_have_required_fields(self):
        corpus = load_corpus()
        required_fields = {"id", "url", "category", "expected_entities", "expected_schemas", "notes"}
        for entry in corpus["urls"]:
            missing = required_fields - set(entry.keys())
            assert not missing, f"URL {entry.get('id', '?')} missing fields: {missing}"

    def test_all_categories_represented(self):
        corpus = load_corpus()
        categories = {entry["category"] for entry in corpus["urls"]}
        missing = REQUIRED_CATEGORIES - categories
        assert not missing, f"Missing categories: {missing}"

    def test_ids_are_unique(self):
        corpus = load_corpus()
        ids = [entry["id"] for entry in corpus["urls"]]
        assert len(ids) == len(set(ids)), "Duplicate IDs found"

    def test_urls_are_valid_https(self):
        corpus = load_corpus()
        for entry in corpus["urls"]:
            assert entry["url"].startswith("https://"), (
                f"URL {entry['id']} must use HTTPS: {entry['url']}"
            )

    def test_expected_entities_is_nonnegative_int(self):
        corpus = load_corpus()
        for entry in corpus["urls"]:
            assert isinstance(entry["expected_entities"], int)
            assert entry["expected_entities"] >= 0

    def test_expected_schemas_is_nonnegative_int(self):
        corpus = load_corpus()
        for entry in corpus["urls"]:
            assert isinstance(entry["expected_schemas"], int)
            assert entry["expected_schemas"] >= 0

    def test_category_counts(self):
        corpus = load_corpus()
        from collections import Counter
        counts = Counter(entry["category"] for entry in corpus["urls"])
        assert counts["static_html"] == 5
        assert counts["js_heavy_spa"] == 4
        assert counts["jsonld_structured"] == 4
        assert counts["api_documentation"] == 4
        assert counts["news_entity_rich"] == 4
        assert counts["ecommerce_product"] == 4
