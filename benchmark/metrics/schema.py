def compute_schema_metrics(
    detected: list[dict],
    ground_truth: dict,
) -> dict:
    """Compute schema detection metrics.

    Args:
        detected: List of {"type": "JSON_LD"|"OPENAPI", "data": dict} from the tool.
        ground_truth: Dict with "schemas" key containing list of expected schemas,
                      each with "type", "schema_org_type", "key_fields", "expected_count".

    Returns dict with keys:
        - jsonld_detection_rate: fraction of expected JSON-LD schemas detected
        - openapi_detection_rate: fraction of expected OpenAPI schemas detected
        - schema_completeness: avg fraction of key_fields present in detected schemas
        - false_positive_rate: fraction of detected schemas not in ground truth
    """
    expected_schemas = ground_truth.get("schemas", [])
    expected_jsonld = [s for s in expected_schemas if s["type"] == "JSON_LD"]
    expected_openapi = [s for s in expected_schemas if s["type"] == "OPENAPI"]

    detected_jsonld = [d for d in detected if d["type"] == "JSON_LD"]
    detected_openapi = [d for d in detected if d["type"] == "OPENAPI"]

    # JSON-LD detection rate
    jsonld_detected_count = 0
    jsonld_completeness_scores = []
    for expected in expected_jsonld:
        found = False
        for det in detected_jsonld:
            if _jsonld_matches(det, expected):
                found = True
                completeness = _compute_completeness(det["data"], expected["key_fields"])
                jsonld_completeness_scores.append(completeness)
                break
        if not found:
            jsonld_completeness_scores.append(0.0)
        else:
            jsonld_detected_count += 1

    jsonld_rate = jsonld_detected_count / len(expected_jsonld) if expected_jsonld else 0.0

    # OpenAPI detection rate
    openapi_detected_count = 0
    openapi_completeness_scores = []
    for expected in expected_openapi:
        found = False
        for det in detected_openapi:
            if _openapi_matches(det):
                found = True
                completeness = _compute_completeness(det["data"], expected["key_fields"])
                openapi_completeness_scores.append(completeness)
                break
        if not found:
            openapi_completeness_scores.append(0.0)
        else:
            openapi_detected_count += 1

    openapi_rate = openapi_detected_count / len(expected_openapi) if expected_openapi else 0.0

    # Overall completeness
    all_completeness = jsonld_completeness_scores + openapi_completeness_scores
    avg_completeness = sum(all_completeness) / len(all_completeness) if all_completeness else 0.0

    # False positive rate
    total_matched = jsonld_detected_count + openapi_detected_count
    total_detected = len(detected)
    unmatched = total_detected - total_matched
    false_positive_rate = unmatched / total_detected if total_detected > 0 else 0.0

    return {
        "jsonld_detection_rate": jsonld_rate,
        "openapi_detection_rate": openapi_rate,
        "schema_completeness": avg_completeness,
        "false_positive_rate": false_positive_rate,
        "jsonld_detected": jsonld_detected_count,
        "jsonld_expected": len(expected_jsonld),
        "openapi_detected": openapi_detected_count,
        "openapi_expected": len(expected_openapi),
    }


def _jsonld_matches(detected: dict, expected: dict) -> bool:
    """Check if a detected JSON-LD schema matches an expected one by @type."""
    data = detected.get("data", {})
    # JSON-LD can be a list of schemas
    if isinstance(data, list):
        return any(_jsonld_matches({"data": item}, expected) for item in data if isinstance(item, dict))
    if not isinstance(data, dict):
        return False
    expected_type = expected.get("schema_org_type")
    if expected_type is None:
        return True
    detected_type = data.get("@type", "")
    return detected_type == expected_type


def _openapi_matches(detected: dict) -> bool:
    """Check if a detected schema looks like an OpenAPI spec."""
    data = detected.get("data", {})
    if isinstance(data, list):
        return any(_openapi_matches({"data": item}) for item in data if isinstance(item, dict))
    if not isinstance(data, dict):
        return False
    return "openapi" in data or "swagger" in data or ("info" in data and "paths" in data)


def _compute_completeness(data, key_fields: list[str]) -> float:
    """Compute fraction of key_fields present in the detected schema data."""
    if not key_fields:
        return 1.0
    if isinstance(data, list):
        # Check if any item in the list has the key fields
        if not data:
            return 0.0
        return max(_compute_completeness(item, key_fields) for item in data if isinstance(item, dict)) if any(isinstance(item, dict) for item in data) else 0.0
    if not isinstance(data, dict):
        return 0.0
    found = sum(1 for field in key_fields if field in data)
    return found / len(key_fields)
