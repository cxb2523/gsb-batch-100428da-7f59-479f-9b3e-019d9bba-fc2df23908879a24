import pytest
from benchmark.metrics.extraction import (
    normalize_text,
    entities_match,
    compute_extraction_metrics,
)


class TestNormalizeText:
    def test_lowercases(self):
        assert normalize_text("Google") == "google"

    def test_strips_whitespace(self):
        assert normalize_text("  Google  ") == "google"

    def test_collapses_internal_whitespace(self):
        assert normalize_text("Tim  Berners  Lee") == "tim berners lee"


class TestEntitiesMatch:
    def test_exact_match(self):
        assert entities_match("Google", "Google") is True

    def test_case_insensitive_match(self):
        assert entities_match("google", "Google") is True

    def test_levenshtein_within_threshold(self):
        # "Gogle" is Levenshtein distance 1 from "Google"
        assert entities_match("Gogle", "Google") is True

    def test_levenshtein_beyond_threshold(self):
        # "Gxxxx" is far from "Google"
        assert entities_match("Gxxxx", "Google") is False

    def test_whitespace_normalization(self):
        assert entities_match("Tim  Berners-Lee", "Tim Berners-Lee") is True


class TestComputeExtractionMetrics:
    def test_perfect_extraction(self):
        predicted = [
            {"text": "Google", "type": "ORG"},
            {"text": "Tim Berners-Lee", "type": "PERSON"},
        ]
        ground_truth = [
            {"text": "Google", "type": "ORG"},
            {"text": "Tim Berners-Lee", "type": "PERSON"},
        ]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        assert metrics["precision"] == pytest.approx(1.0)
        assert metrics["recall"] == pytest.approx(1.0)
        assert metrics["f1"] == pytest.approx(1.0)
        assert metrics["type_accuracy"] == pytest.approx(1.0)

    def test_partial_match(self):
        predicted = [
            {"text": "Google", "type": "ORG"},
            {"text": "Microsoft", "type": "ORG"},
            {"text": "Random Corp", "type": "ORG"},
        ]
        ground_truth = [
            {"text": "Google", "type": "ORG"},
            {"text": "Microsoft", "type": "ORG"},
            {"text": "Apple", "type": "ORG"},
        ]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        # TP=2 (Google, Microsoft), FP=1 (Random Corp), FN=1 (Apple)
        assert metrics["precision"] == pytest.approx(2.0 / 3.0)
        assert metrics["recall"] == pytest.approx(2.0 / 3.0)
        assert metrics["true_positives"] == 2
        assert metrics["false_positives"] == 1
        assert metrics["false_negatives"] == 1

    def test_no_matches(self):
        predicted = [{"text": "Foo", "type": "ORG"}]
        ground_truth = [{"text": "Bar", "type": "ORG"}]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0

    def test_empty_predicted(self):
        ground_truth = [{"text": "Google", "type": "ORG"}]
        metrics = compute_extraction_metrics([], ground_truth)
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0

    def test_empty_ground_truth(self):
        predicted = [{"text": "Google", "type": "ORG"}]
        metrics = compute_extraction_metrics(predicted, [])
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0

    def test_both_empty(self):
        metrics = compute_extraction_metrics([], [])
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0

    def test_type_accuracy_wrong_types(self):
        predicted = [
            {"text": "Google", "type": "PERSON"},  # wrong type
            {"text": "Tim Berners-Lee", "type": "PERSON"},
        ]
        ground_truth = [
            {"text": "Google", "type": "ORG"},
            {"text": "Tim Berners-Lee", "type": "PERSON"},
        ]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        # Both text-match, but one has wrong type
        assert metrics["type_accuracy"] == pytest.approx(0.5)

    def test_fuzzy_match_counts_as_tp(self):
        predicted = [{"text": "Gogle", "type": "ORG"}]  # typo
        ground_truth = [{"text": "Google", "type": "ORG"}]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        assert metrics["true_positives"] == 1
        assert metrics["precision"] == pytest.approx(1.0)
        assert metrics["recall"] == pytest.approx(1.0)
