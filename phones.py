#!/usr/bin/env python3
"""Phone-number extraction from bio text (supports Arabic/Persian and Latin digits)."""

import re

# Map Arabic / Persian digits to Latin digits.
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_TRANS = {ord(a): str(i) for i, a in enumerate(_ARABIC_DIGITS)}
_TRANS.update({ord(p): str(i) for i, p in enumerate(_PERSIAN_DIGITS)})

# Words/emojis that hint at a contact number (kept for optional confidence scoring).
_CONTACT_HINTS = re.compile(
    r"(whats?app|wa\.me|واتس|تواصل|للتواصل|رقم|جوال|موبايل|هاتف|اتصال|📞|☎|📱|📲|🟢)",
    re.IGNORECASE,
)

# A phone-like sequence: may start with + or 00, then digits with common separators.
_PHONE_CANDIDATE = re.compile(
    r"(?<!\w)(?:\+|00)?\s?(?:\d[\d\s\-().]{6,18}\d)"
)


def normalize_digits(text: str) -> str:
    """Convert Arabic/Persian digits to Latin digits."""
    return (text or "").translate(_TRANS)


def _clean(raw: str) -> str:
    """Keep only digits and a leading +, converting a 00 prefix into +."""
    plus = raw.strip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
        plus = True
    return ("+" + digits) if plus else digits


def extract_phones(text: str):
    """Return a de-duplicated list of phone numbers found in the text."""
    if not text:
        return []
    norm = normalize_digits(text)
    found = []
    seen = set()
    for match in _PHONE_CANDIDATE.finditer(norm):
        cleaned = _clean(match.group(0))
        core = cleaned.lstrip("+")
        # Plausible phone length: 8 to 15 digits.
        if not (8 <= len(core) <= 15):
            continue
        if core in seen:
            continue
        seen.add(core)
        found.append(cleaned)
    return found


def has_phone(text: str) -> bool:
    return bool(extract_phones(text))


if __name__ == "__main__":
    samples = [
        "للتواصل واتساب ٠١٠١٢٣٤٥٦٧٨",
        "رقمي 0501234567 للطلبات",
        "call me +20 100 123 4567",
        "no phone here just 2023 year",
        "insta @someone",
    ]
    for s in samples:
        print(repr(s), "->", extract_phones(s))
