"""PaddleOCR wrapper with WeChat UI artifact filtering.

Provides extract_text() as a standalone function that can be called
without a MessageDetector instance.  Maintains a module-level OCR
instance cache for performance (PaddleOCR model is loaded once).
"""
import logging
import re
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance

logger = logging.getLogger(__name__)

# ── module-level OCR cache ─────────────────────────────────────────────────
_ocr_instance: Optional["PaddleOCR"] = None


def _get_ocr(lang: str = "ch") -> "PaddleOCR":
    """Lazy-load and cache the PaddleOCR singleton."""
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        logger.info("Loading PaddleOCR...")
        _ocr_instance = PaddleOCR(lang=lang)
        logger.info("PaddleOCR ready")
    return _ocr_instance


# ── UI artifact patterns ───────────────────────────────────────────────────

_DATE_ARTIFACT_RE = re.compile(
    r'^(周[一二三四五六日]|星期[一二三四五六日]|期[一二三四五六日])\s*'
)

_UI_ARTIFACTS = [
    "√转换完成", "✓转换完成", "转换完成",
    "√ 转换完成", "✓ 转换完成",
]


def strip_ocr_artifacts(text: str) -> str:
    """Remove WeChat UI labels misread by PaddleOCR (dates, voice-to-text markers)."""
    text = _DATE_ARTIFACT_RE.sub('', text, count=1)
    for artifact in _UI_ARTIFACTS:
        text = text.replace(artifact, "")
    return re.sub(r'\s{2,}', ' ', text).strip()


# ── config defaults ────────────────────────────────────────────────────────

OCR_DEFAULTS = {
    "lang": "ch",
    "min_image_height": 50,
    "enhance_contrast": False,
    "contrast_factor": 2.0,
    "timestamp_filter_pattern": r'^[\d:;.\-\s]+$',
    "default_confidence": 0.4,
}


# ── main API ───────────────────────────────────────────────────────────────

def extract_text(
    img: Image.Image,
    config: dict = None,
) -> str:
    """Run PaddleOCR on an image and return cleaned text.

    Args:
        img: PIL Image (RGB) to OCR.
        config: Optional dict overriding ``OCR_DEFAULTS`` keys.

    Returns:
        Cleaned text string, or ``""`` if no text found.
    """
    cfg = dict(OCR_DEFAULTS)
    if config:
        cfg.update(config)

    min_conf = cfg["default_confidence"]
    ocr_lang = cfg["lang"]
    min_h = cfg["min_image_height"]
    enhance = cfg["enhance_contrast"]
    contrast = cfg["contrast_factor"]
    ts_pattern = cfg["timestamp_filter_pattern"]

    w, h = img.size
    if h < min_h:
        target_h = min_h
        img = img.resize((int(w * target_h / h), target_h), Image.LANCZOS)
    if enhance:
        img = ImageEnhance.Contrast(img).enhance(contrast)

    arr = np.array(img)
    ocr = _get_ocr(lang=ocr_lang)
    result = ocr.ocr(arr)
    if not result or not result[0]:
        return ""

    ts_re = re.compile(ts_pattern)
    texts = []
    for r in result[0]:
        text, conf = r[1]
        if conf < min_conf:
            continue
        t = text.strip()
        if ts_re.match(t):
            continue
        if len(t) <= 1 and not re.search(r'[一-鿿]', t):
            continue
        texts.append(t)

    combined = " ".join(texts)
    return strip_ocr_artifacts(combined)


def clear_ocr_cache():
    """Reset the global PaddleOCR instance (useful for testing)."""
    global _ocr_instance
    _ocr_instance = None
