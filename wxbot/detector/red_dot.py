"""Red dot detection in WeChat chat list column.

Scans the left portion of column 2 (chat list) for red pixel clusters
indicating unread messages. Extracted from the original MessageDetector class.

Original signature: detect_red_dots(self, img: Image.Image) -> List[int]
New signature: detect_red_dots(img: Image.Image, config: dict = None) -> List[DetectorResult]

Each DetectorResult carries a cluster ``signature`` in ``metadata`` —
``(cluster_size, x_min, y_min, x_max, y_max)`` — so the scan scheduler can
tell "a NEW red dot appeared on the same row" apart from "the OLD red dot
has not disappeared yet" and decide whether to bypass the click cooldown.
"""
from typing import List, Optional

from PIL import Image
import numpy as np

from wxbot.detector import DetectorResult


# Default thresholds (overridable via config dict)
DEFAULTS = {
    "scan_width_ratio": 0.05,
    "scan_width_min_px": 20,
    "r_min": 90,
    "r_max": 220,
    "gb_max": 130,
    "min_pixels": 2,
    "red_excess_g": 20,
    "red_excess_b": 20,
    "cluster_gap": 5,
    "cluster_min_points": 2,
}

# Signature layout: (pixel_count, x_min, y_min, x_max, y_max)
SIGNATURE_FIELDS = 5


def detect_red_dots(img: Image.Image, config: dict = None) -> List[DetectorResult]:
    """Detect red dot clusters in the chat list screenshot.

    Scans the left ``scan_width_ratio`` portion of the image for red pixels
    (R between r_min/r_max, G/B below gb_max, R exceeding G/B by excess
    thresholds).  Detected pixels are clustered by Y proximity and filtered
    by minimum cluster size.

    Args:
        img: Column-2 screenshot as PIL Image (RGB).  ``None`` or a
            zero-sized image safely yields an empty list (window not
            found / empty capture).
        config: Optional dict overriding any DEFAULTS key.

    Returns:
        List of DetectorResult, one per red-dot cluster.  The ``metadata``
        dict carries:

        - ``y_center`` (int): cluster vertical center (legacy position).
        - ``cluster_size`` (int): number of red pixels in the cluster.
        - ``cluster_bbox`` (tuple): actual pixel bbox
          ``(x_min, y_min, x_max, y_max)`` of the cluster.
        - ``signature`` (tuple): ``(cluster_size, x_min, y_min, x_max,
          y_max)`` — comparable via :func:`signatures_match` to decide
          whether the row's red dot changed since the last click.
    """
    if img is None:
        return []

    cfg = dict(DEFAULTS)
    if config:
        cfg.update(config)

    scan_w_ratio = cfg["scan_width_ratio"]
    scan_w_min = cfg["scan_width_min_px"]
    r_min = cfg["r_min"]
    r_max = cfg["r_max"]
    gb_max = cfg["gb_max"]
    min_px = cfg["min_pixels"]
    red_excess_g = cfg["red_excess_g"]
    red_excess_b = cfg["red_excess_b"]
    cluster_gap = cfg["cluster_gap"]
    cluster_min = cfg["cluster_min_points"]

    w, h = img.size
    if w == 0 or h == 0:
        return []

    pixels = img.load()
    scan_w = max(int(w * scan_w_ratio), scan_w_min)

    dots = []
    for y in range(h):
        row_dots = []
        for x in range(scan_w):
            r, g, b = pixels[x, y][:3]
            if (r_min < r < r_max
                    and g < gb_max and b < gb_max
                    and r > g + red_excess_g
                    and r > b + red_excess_b):
                row_dots.append((x, y))
        if len(row_dots) >= min_px:
            dots.extend(row_dots)

    if not dots:
        return []

    dots.sort(key=lambda d: d[1])
    clusters, cur = [], [dots[0]]
    for i in range(1, len(dots)):
        if abs(dots[i][1] - cur[-1][1]) < cluster_gap:
            cur.append(dots[i])
        else:
            if len(cur) >= cluster_min:
                clusters.append(_cluster_result(cur, cluster_min))
            cur = [dots[i]]
    if len(cur) >= cluster_min:
        clusters.append(_cluster_result(cur, cluster_min))

    return clusters


def _cluster_result(cluster: list, cluster_min: int) -> DetectorResult:
    """Build a DetectorResult for one cluster, including its signature."""
    y_center = sum(d[1] for d in cluster) // len(cluster)
    x_min = min(d[0] for d in cluster)
    x_max = max(d[0] for d in cluster)
    y_min = min(d[1] for d in cluster)
    y_max = max(d[1] for d in cluster)
    bbox = (x_min, y_min, x_max, y_max)
    signature = (len(cluster), x_min, y_min, x_max, y_max)
    return DetectorResult(
        detected=True,
        confidence=_compute_confidence(cluster, cluster_min),
        bbox=bbox,
        text="",
        metadata={
            "y_center": y_center,
            "cluster_size": len(cluster),
            "cluster_bbox": bbox,
            "signature": signature,
        },
    )


def _compute_confidence(cluster: list, min_points: int) -> float:
    """Heuristic confidence: larger clusters relative to minimum = higher confidence."""
    ratio = len(cluster) / max(min_points, 1)
    if ratio >= 10:
        return 1.0
    elif ratio >= 5:
        return 0.9
    elif ratio >= 3:
        return 0.8
    elif ratio >= 2:
        return 0.6
    return 0.4


def cluster_signature(result: DetectorResult) -> Optional[tuple]:
    """Extract the comparable signature from a red-dot DetectorResult."""
    if result is None:
        return None
    return result.metadata.get("signature")


def signatures_match(sig_a: Optional[tuple], sig_b: Optional[tuple],
                     tolerance_px: int = 0) -> bool:
    """True if two red-dot signatures describe the same cluster.

    Signatures compare field-by-field (pixel count + bbox corners); every
    field must differ by no more than ``tolerance_px``.  Anything else —
    a bigger cluster, a shifted bbox — counts as NEW content.  Missing or
    malformed signatures never match (no evidence → no bypass decision
    based on them).
    """
    if not sig_a or not sig_b:
        return False
    if len(sig_a) != SIGNATURE_FIELDS or len(sig_b) != SIGNATURE_FIELDS:
        return False
    return all(abs(int(a) - int(b)) <= tolerance_px
               for a, b in zip(sig_a, sig_b))


# Legacy-compatible wrapper returning plain y-centers
def detect_red_dots_legacy(img: Image.Image, config: dict = None) -> List[int]:
    """Return list of Y-center positions (original MessageDetector format)."""
    results = detect_red_dots(img, config)
    return [r.metadata["y_center"] for r in results]
