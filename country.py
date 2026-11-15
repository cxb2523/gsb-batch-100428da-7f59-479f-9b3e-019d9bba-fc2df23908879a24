#!/usr/bin/env python3
"""
Guess the country of a phone number with probabilities (hybrid algorithm):

  1) Explicit international code (+ / 00 / a known code at the start) => high confidence.
  2) Local-format inference (national mobile prefixes):
       - 01[0125] with length 11  => Egypt.
       - 05x with length 10       => Saudi Arabia / UAE (overlapping) => probability split.
       - 920 / 9200 / 800         => Saudi unified/toll numbers.
       - 8 digits without a leading 0 => Gulf 8-digit numbering (Bahrain/Qatar/Kuwait...).
  3) Signals from the bio text boost a candidate:
       - Country flag emojis (🇸🇦 🇦🇪 🇪🇬 ...).
       - Country/city names (Riyadh, Jeddah, Dubai, Cairo, Kuwait ...).
       - An international code written inside the text (+966, 0097 1 ...).

Output: best country (English + Arabic name), ISO code, dial code, confidence,
probability distribution (keyed by ISO), and short reasons.
"""

import re
from collections import defaultdict


# ISO -> (English name, Arabic name, dial code, flag)
COUNTRIES = {
    "SA": ("Saudi Arabia", "السعودية", "+966", "🇸🇦"),
    "AE": ("United Arab Emirates", "الإمارات", "+971", "🇦🇪"),
    "EG": ("Egypt", "مصر", "+20", "🇪🇬"),
    "BH": ("Bahrain", "البحرين", "+973", "🇧🇭"),
    "QA": ("Qatar", "قطر", "+974", "🇶🇦"),
    "KW": ("Kuwait", "الكويت", "+965", "🇰🇼"),
    "OM": ("Oman", "عُمان", "+968", "🇴🇲"),
    "YE": ("Yemen", "اليمن", "+967", "🇾🇪"),
    "JO": ("Jordan", "الأردن", "+962", "🇯🇴"),
    "LB": ("Lebanon", "لبنان", "+961", "🇱🇧"),
    "SY": ("Syria", "سوريا", "+963", "🇸🇾"),
    "IQ": ("Iraq", "العراق", "+964", "🇮🇶"),
    "PS": ("Palestine", "فلسطين", "+970", "🇵🇸"),
    "LY": ("Libya", "ليبيا", "+218", "🇱🇾"),
    "TN": ("Tunisia", "تونس", "+216", "🇹🇳"),
    "DZ": ("Algeria", "الجزائر", "+213", "🇩🇿"),
    "MA": ("Morocco", "المغرب", "+212", "🇲🇦"),
    "SD": ("Sudan", "السودان", "+249", "🇸🇩"),
    "MR": ("Mauritania", "موريتانيا", "+222", "🇲🇷"),
    "TR": ("Turkey", "تركيا", "+90", "🇹🇷"),
    "IR": ("Iran", "إيران", "+98", "🇮🇷"),
    "PK": ("Pakistan", "باكستان", "+92", "🇵🇰"),
    "IN": ("India", "الهند", "+91", "🇮🇳"),
    "BD": ("Bangladesh", "بنغلاديش", "+880", "🇧🇩"),
    "ID": ("Indonesia", "إندونيسيا", "+62", "🇮🇩"),
    "MY": ("Malaysia", "ماليزيا", "+60", "🇲🇾"),
    "NG": ("Nigeria", "نيجيريا", "+234", "🇳🇬"),
    "CN": ("China", "الصين", "+86", "🇨🇳"),
    "US": ("United States", "الولايات المتحدة", "+1", "🇺🇸"),
    "GB": ("United Kingdom", "بريطانيا", "+44", "🇬🇧"),
    "FR": ("France", "فرنسا", "+33", "🇫🇷"),
    "DE": ("Germany", "ألمانيا", "+49", "🇩🇪"),
}

# Dial code -> ISO (matched longest-first to avoid overlaps like 20 / 212).
_DIAL_TO_ISO = {}
for _iso, (_en, _ar, _dial, _flag) in COUNTRIES.items():
    _DIAL_TO_ISO[_dial.lstrip("+")] = _iso

# Flag -> ISO
_FLAG_TO_ISO = {flag: iso for iso, (_en, _ar, _d, flag) in COUNTRIES.items()}

# Country/city keywords (Arabic + English) -> ISO
_KEYWORDS = {
    "SA": ["السعودية", "السعوديه", "سعودي", "الرياض", "جده", "جدة", "الدمام", "مكة",
           "مكه", "المدينة", "المدينه", "الخبر", "القصيم", "بريدة", "بريده", "تبوك",
           "الطائف", "الطايف", "حائل", "ابها", "أبها", "نجران", "جازان", "saudi",
           "riyadh", "jeddah", "dammam", "ksa"],
    "AE": ["الامارات", "الإمارات", "دبي", "ابوظبي", "أبوظبي", "الشارقة", "الشارقه",
           "عجمان", "العين", "رأس الخيمة", "الفجيرة", "ام القيوين", "uae", "dubai",
           "abudhabi", "sharjah", "emirates"],
    "EG": ["مصر", "القاهرة", "القاهره", "الاسكندرية", "الإسكندرية", "اسكندرية",
           "الجيزة", "الجيزه", "المنصورة", "طنطا", "اسيوط", "أسيوط", "egypt", "cairo"],
    "BH": ["البحرين", "المنامة", "المنامه", "المحرق", "bahrain", "manama"],
    "QA": ["قطر", "الدوحة", "الدوحه", "qatar", "doha"],
    "KW": ["الكويت", "kuwait"],
    "OM": ["عمان", "عُمان", "مسقط", "oman", "muscat", "صلالة"],
    "YE": ["اليمن", "صنعاء", "عدن", "yemen", "sanaa"],
    "JO": ["الاردن", "الأردن", "عمّان", "jordan", "amman"],
    "LB": ["لبنان", "بيروت", "lebanon", "beirut"],
    "SY": ["سوريا", "سورية", "دمشق", "حلب", "syria", "damascus"],
    "IQ": ["العراق", "بغداد", "البصرة", "iraq", "baghdad"],
    "PS": ["فلسطين", "غزة", "غزه", "الضفة", "palestine", "gaza"],
    "LY": ["ليبيا", "طرابلس", "بنغازي", "libya"],
    "TN": ["تونس", "tunisia", "tunis"],
    "DZ": ["الجزائر", "algeria"],
    "MA": ["المغرب", "الرباط", "الدار البيضاء", "morocco", "casablanca"],
    "SD": ["السودان", "الخرطوم", "sudan", "khartoum"],
    "TR": ["تركيا", "اسطنبول", "إسطنبول", "انقرة", "turkey", "istanbul", "türkiye"],
    "PK": ["باكستان", "pakistan"],
    "IN": ["الهند", "india"],
    "MY": ["ماليزيا", "malaysia"],
    "NG": ["نيجيريا", "nigeria"],
}

# Scoring weights.
W_INTL = 100.0        # explicit international code
W_NATIONAL = 8.0      # strong national-prefix match (Egypt / Saudi-only)
W_FLAG = 6.0          # country flag in bio
W_KEYWORD = 4.0       # country/city name in bio
W_INTL_IN_BIO = 12.0  # international code written in bio text

# 05x mobile prefixes: which countries are possible (10 digits total).
_SA_ONLY_05 = {"053", "057", "059", "051"}
_AE_ONLY_05 = {"052"}
_SHARED_05 = {"050", "054", "055", "056", "058"}


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _match_calling_code(digits, min_len=1):
    """Match the longest known dial code at the start of the digits.

    min_len: shortest dial-code length allowed. For local inference we avoid
    single-digit codes such as +1 because they collide with local numbers
    starting with 1.
    """
    for ln in (4, 3, 2, 1):
        if ln < min_len:
            continue
        if len(digits) > ln and digits[:ln] in _DIAL_TO_ISO:
            return _DIAL_TO_ISO[digits[:ln]], digits[:ln]
    return None, None


def _national_scores(digits, scores, reasons):
    """Infer from local format (numbers usually start with a trunk 0)."""
    n = len(digits)
    # Saudi unified / toll numbers.
    if digits.startswith(("920", "9200", "800", "8001")):
        scores["SA"] += W_NATIONAL
        reasons.append("Saudi unified number (920/800)")
        return
    # Egypt: 01[0125] + 8 = 11 digits.
    if n == 11 and digits.startswith("01") and digits[2] in "0125":
        scores["EG"] += W_NATIONAL
        reasons.append("Egyptian mobile 01" + digits[2])
        return
    # Gulf: 05x + 7/8 = 10 digits (Saudi / UAE).
    if n == 10 and digits.startswith("05"):
        p3 = digits[:3]
        if p3 in _SA_ONLY_05:
            scores["SA"] += W_NATIONAL
            reasons.append(f"prefix {p3} is Saudi")
        elif p3 in _AE_ONLY_05:
            scores["AE"] += W_NATIONAL
            reasons.append(f"prefix {p3} is UAE")
        elif p3 in _SHARED_05:
            scores["SA"] += 4.0
            scores["AE"] += 3.0
            reasons.append(f"prefix {p3} shared (Saudi/UAE)")
        else:
            scores["SA"] += 3.0
            scores["AE"] += 2.0
            reasons.append("local Gulf 05x prefix")
        return
    # 8 digits without a leading 0: Gulf 8-digit numbering.
    if n == 8 and not digits.startswith("0"):
        c = digits[0]
        if c == "3":
            scores["BH"] += 3.0
            scores["QA"] += 3.0
            reasons.append("8 digits starting with 3 (Bahrain/Qatar)")
        elif c in "5679":
            scores["KW"] += 3.0
            scores["QA"] += 2.0
            reasons.append("8 digits (Kuwait/Qatar)")
        else:
            scores["BH"] += 1.0
            scores["QA"] += 1.0
            scores["KW"] += 1.0
            reasons.append("possible Gulf 8-digit numbering")
        return
    # Last resort: dial code without + (ignore numbers with a leading 0 and 1-digit codes).
    if not digits.startswith("0"):
        iso, code = _match_calling_code(digits, min_len=2)
        if iso:
            scores[iso] += W_NATIONAL
            reasons.append(f"starts with dial code {code} (no +)")


def _bio_context(bio, scores, reasons):
    if not bio:
        return
    low = bio.lower()
    # Flags.
    for flag in re.findall(r"[\U0001F1E6-\U0001F1FF]{2}", bio):
        iso = _FLAG_TO_ISO.get(flag)
        if iso:
            scores[iso] += W_FLAG
            reasons.append(f"flag {flag} in bio")
    # International code inside a phone-like sequence (+966... / 0097339...).
    # Require at least 7 digits after +/00 so we don't catch a stray "+1" or "00".
    norm = re.sub(r"[ \-().]", "", bio)
    for seq in re.findall(r"(?<!\d)(?:\+|00)(\d{7,15})", norm):
        iso, code = _match_calling_code(seq, min_len=1)
        if iso:
            scores[iso] += W_INTL_IN_BIO
            reasons.append(f"dial code +{code} mentioned in text")
    # Country/city keywords.
    for iso, words in _KEYWORDS.items():
        for w in words:
            if w in bio or w in low:
                scores[iso] += W_KEYWORD
                reasons.append(f"mentions '{w}' ({COUNTRIES[iso][0]})")
                break


def detect_country(phone, bio=""):
    """Return a dict with the best country guess and a probability distribution."""
    scores = defaultdict(float)
    reasons = []
    raw = (phone or "").strip()
    digits = _digits(raw)
    if not digits:
        return {"country": None, "country_ar": None, "iso": None, "dial_code": None,
                "flag": None, "confidence": 0.0, "probabilities": {},
                "reasons": ["no digits"]}

    is_intl = raw.startswith("+") or raw.startswith("00")
    if is_intl:
        iso, code = _match_calling_code(digits)
        if iso:
            scores[iso] += W_INTL
            reasons.append(f"international code +{code}")
        else:
            _national_scores(digits, scores, reasons)
    else:
        _national_scores(digits, scores, reasons)

    _bio_context(bio, scores, reasons)

    if not scores:
        return {"country": None, "country_ar": None, "iso": None, "dial_code": None,
                "flag": None, "confidence": 0.0, "probabilities": {},
                "reasons": reasons or ["unknown"]}

    total = sum(scores.values())
    probs = {iso: round(s / total, 3) for iso, s in scores.items()}
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    best_iso = ranked[0][0]
    return {
        "country": COUNTRIES[best_iso][0],
        "country_ar": COUNTRIES[best_iso][1],
        "iso": best_iso,
        "dial_code": COUNTRIES[best_iso][2],
        "flag": COUNTRIES[best_iso][3],
        "confidence": ranked[0][1],
        "probabilities": dict(ranked),
        "reasons": reasons,
    }


if __name__ == "__main__":
    tests = [
        ("+97339079094", "Start your business in Bahrain"),
        ("01012483870", "الاصول يا تتربي عليها"),
        ("0533777529", "الرياض حي ظهرة لبن"),
        ("0547166686", "منطقة بور سعيد ديرة دبي"),
        ("920011924", "روح المذاق المصري 🇪🇬 🇸🇦"),
        ("0555215522", "delivery 🇦🇪 dubai"),
        ("0555215522", ""),
        ("+905301234567", "istanbul turkey"),
    ]
    for ph, bio in tests:
        r = detect_country(ph, bio)
        print(f"{ph:16} -> {r['country']} ({r['confidence']}) {r['probabilities']}")
