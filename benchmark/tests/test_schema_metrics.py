import pytest
from benchmark.metrics.schema import compute_schema_metrics


class TestComputeSchemaMetrics:
    def test_perfect_jsonld_detection(self):
        detected = [{"type": "JSON_LD", "data": {"@type": "Recipe", "name": "Cake", "author": "Chef"}}]
        ground_truth = {
            "schemas": [
                {
                    "type": "JSON_LD",
                    "schema_org_type": "Recipe",
                    "key_fields": ["name", "author"],
                    "expected_count": 1,
                }
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["jsonld_detection_rate"] == pytest.approx(1.0)
        assert metrics["schema_completeness"] == pytest.approx(1.0)

    def test_partial_completeness(self):
        detected = [{"type": "JSON_LD", "data": {"@type": "Recipe", "name": "Cake"}}]
        ground_truth = {
            "schemas": [
                {
                    "type": "JSON_LD",
                    "schema_org_type": "Recipe",
                    "key_fields": ["name", "author", "cookTime"],
                    "expected_count": 1,
                }
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["jsonld_detection_rate"] == pytest.approx(1.0)
        # Only "name" found out of 3 key_fields
        assert metrics["schema_completeness"] == pytest.approx(1.0 / 3.0)

    def test_no_detection(self):
        detected = []
        ground_truth = {
            "schemas": [
                {
                    "type": "JSON_LD",
                    "schema_org_type": "Recipe",
                    "key_fields": ["name"],
                    "expected_count": 1,
                }
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["jsonld_detection_rate"] == pytest.approx(0.0)
        assert metrics["schema_completeness"] == pytest.approx(0.0)

    def test_openapi_detection(self):
        detected = [{"type": "OPENAPI", "data": {"openapi": "3.0.0", "info": {}, "paths": {}}}]
        ground_truth = {
            "schemas": [
                {
                    "type": "OPENAPI",
                    "schema_org_type": None,
                    "key_fields": ["openapi", "info", "paths"],
                    "expected_count": 1,
                }
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["openapi_detection_rate"] == pytest.approx(1.0)
        assert metrics["schema_completeness"] == pytest.approx(1.0)

    def test_false_positive(self):
        detected = [{"type": "JSON_LD", "data": {"@type": "Product"}}]
        ground_truth = {"schemas": []}
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["false_positive_rate"] == pytest.approx(1.0)

    def test_no_schemas_expected_none_found(self):
        detected = []
        ground_truth = {"schemas": []}
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["false_positive_rate"] == pytest.approx(0.0)
        assert metrics["jsonld_detection_rate"] == pytest.approx(0.0)
        assert metrics["openapi_detection_rate"] == pytest.approx(0.0)

    def test_empty_ground_truth_dict(self):
        detected = []
        ground_truth = {"schemas": []}
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["schema_completeness"] == pytest.approx(0.0)

    def test_mixed_schema_types(self):
        detected = [
            {"type": "JSON_LD", "data": {"@type": "Recipe", "name": "Cake"}},
            {"type": "OPENAPI", "data": {"openapi": "3.0.0", "info": {}, "paths": {}}},
        ]
        ground_truth = {
            "schemas": [
                {
                    "type": "JSON_LD",
                    "schema_org_type": "Recipe",
                    "key_fields": ["name"],
                    "expected_count": 1,
                },
                {
                    "type": "OPENAPI",
                    "schema_org_type": None,
                    "key_fields": ["openapi", "info", "paths"],
                    "expected_count": 1,
                },
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["jsonld_detection_rate"] == pytest.approx(1.0)
        assert metrics["openapi_detection_rate"] == pytest.approx(1.0)
