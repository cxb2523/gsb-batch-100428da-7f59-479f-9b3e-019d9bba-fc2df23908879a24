import re

from Levenshtein import distance as levenshtein_distance

LEVENSHTEIN_THRESHOLD = 2


def normalize_text(text: str) -> str:
    """Normalize entity text for comparison: lowercase, strip, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def entities_match(predicted_text: str, ground_truth_text: str) -> bool:
    """Check if two entity texts match within Levenshtein threshold."""
    a = normalize_text(predicted_text)
    b = normalize_text(ground_truth_text)
    if a == b:
        return True
    return levenshtein_distance(a, b) <= LEVENSHTEIN_THRESHOLD


def compute_extraction_metrics(
    predicted: list[dict],
    ground_truth: list[dict],
) -> dict:
    """Compute entity extraction quality metrics.

    Args:
        predicted: List of {"text": str, "type": str} from the tool.
        ground_truth: List of {"text": str, "type": str} from annotations.

    Returns dict with keys:
        - precision, recall, f1: standard IR metrics based on text matching
        - type_accuracy: of text-matched entities, fraction with correct type
        - true_positives, false_positives, false_negatives: raw counts
    """
    if not predicted and not ground_truth:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "type_accuracy": 0.0,
            "true_positives": 0,
            "false_positives": 0,
            "false_negatives": 0,
        }

    if not predicted:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "type_accuracy": 0.0,
            "true_positives": 0,
            "false_positives": len(predicted),
            "false_negatives": len(ground_truth),
        }

    if not ground_truth:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "type_accuracy": 0.0,
            "true_positives": 0,
            "false_positives": len(predicted),
            "false_negatives": 0,
        }

    # Greedy matching: for each predicted entity, find the best ground truth match
    gt_matched = [False] * len(ground_truth)
    tp = 0
    type_correct = 0

    for pred in predicted:
        best_idx = None
        for i, gt in enumerate(ground_truth):
            if gt_matched[i]:
                continue
            if entities_match(pred["text"], gt["text"]):
                best_idx = i
                break

        if best_idx is not None:
            gt_matched[best_idx] = True
            tp += 1
            if pred["type"] == ground_truth[best_idx]["type"]:
                type_correct += 1

    fp = len(predicted) - tp
    fn = len(ground_truth) - tp

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    type_accuracy = type_correct / tp if tp > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "type_accuracy": type_accuracy,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }
