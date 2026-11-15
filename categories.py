#!/usr/bin/env python3
"""
Automatically classify an account's business type from its bio (and name) text
using a weighted-keyword algorithm.

Idea: each category has a list of distinctive keywords/phrases with weights
(specific phrases weigh more than generic ones). We sum the scores, normalize
them into probabilities, and return the top category plus ranked tags and a
confidence value.

Note: the keyword lists are mostly Arabic on purpose, because the target bios
are Arabic. The output labels are provided in both English and Arabic.

Output: {category, category_ar, confidence, tags:[key,...], scores:{key:prob}}
"""

import re
from collections import defaultdict

# key -> (Arabic label, English label)
CATEGORIES = {
    "real_estate":   ("عقارات", "Real Estate"),
    "cars":          ("سيارات", "Cars & Auto"),
    "delivery":      ("توصيل وشحن", "Delivery & Shipping"),
    "marketing":     ("تسويق وإعلان", "Marketing & Ads"),
    "restaurant":    ("مطاعم وأطعمة", "Restaurants & Food"),
    "clothing":      ("ملابس وأزياء", "Clothing & Fashion"),
    "beauty":        ("تجميل وعناية", "Beauty & Care"),
    "medical":       ("طبي وصحة", "Medical & Health"),
    "education":     ("تعليم وتدريب", "Education & Training"),
    "cleaning":      ("تنظيف ومكافحة", "Cleaning & Pest"),
    "furniture":     ("أثاث وديكور", "Furniture & Decor"),
    "contracting":   ("مقاولات وبناء", "Contracting & Construction"),
    "printing":      ("طباعة ودعاية", "Printing & Advertising"),
    "tech":          ("تقنية وإلكترونيات", "Tech & Electronics"),
    "finance":       ("مالية وأعمال", "Finance & Business"),
    "travel":        ("سياحة وسفر", "Travel & Tourism"),
    "store":         ("متجر وتجارة", "Store & Retail"),
}

# key -> [(keyword/phrase, weight), ...]  (specific phrases 2-3, generic 1)
_KW = {
    "real_estate": [
        ("عقار", 3), ("عقارات", 3), ("عقاري", 3), ("عقارية", 3), ("العقاري", 3),
        ("real estate", 3), ("estate", 2), ("property", 2),
        ("شقق", 3), ("شقة", 2), ("فلل", 3), ("فيلا", 2), ("عمائر", 2), ("عمارة", 2),
        ("اراضي", 3), ("أراضي", 3), ("ارض", 1), ("مخطط", 2), ("مخططات", 2),
        ("ايجار", 2), ("للايجار", 3), ("للإيجار", 3), ("تمليك", 3), ("للبيع والايجار", 3),
        ("استراحة", 2), ("استراحات", 2), ("صك", 2), ("وسيط عقاري", 3), ("تسويق عقاري", 3),
        ("دور", 1), ("روف", 2), ("شاليه", 2), ("مزرعة", 2), ("بيوت", 1),
    ],
    "cars": [
        ("سيارات", 3), ("سياره", 2), ("سيارة", 2), ("معرض سيارات", 3), ("قطع غيار", 3),
        ("تشليح", 3), ("ورشة", 2), ("كماليات", 2), ("زينة سيارات", 3), ("تظليل", 2),
        ("مرسيدس", 2), ("تويوتا", 2), ("هونداي", 2), ("لكزس", 2), ("رنج", 2), ("رنچ", 2),
        ("دباب", 2), ("دبابات", 2), ("موتوسيكل", 2), ("موتور", 1),
        ("car", 2), ("cars", 3), ("auto", 2), ("motors", 2), ("تأجير سيارات", 3),
    ],
    "delivery": [
        ("توصيل", 3), ("توصيل طلبات", 3), ("مندوب توصيل", 3), ("دليفري", 3), ("delivery", 3),
        ("شحن", 3), ("شحن دولي", 3), ("شحنات", 2), ("shipping", 3), ("نقل عفش", 3),
        ("نقل اثاث", 3), ("مندوب", 2), ("توصيل سريع", 3), ("سطحة", 2), ("نقل", 1),
    ],
    "marketing": [
        ("تسويق", 3), ("مسوق", 3), ("للتسويق", 3), ("تسويق الكتروني", 3), ("marketing", 3),
        ("اعلان", 2), ("اعلانات", 3), ("ads", 2), ("سوشيال ميديا", 3), ("social media", 3),
        ("ادارة حسابات", 3), ("ادارة الحسابات", 3), ("محتوى", 2), ("مؤثر", 2), ("بروموشن", 2),
        ("حملات اعلانية", 3), ("سناب", 1), ("branding", 2), ("براندنق", 2),
    ],
    "restaurant": [
        ("مطعم", 3), ("مطاعم", 3), ("كافيه", 3), ("كوفي", 2), ("cafe", 2), ("coffee", 2),
        ("restaurant", 3), ("مقهى", 2), ("بوفيه", 3), ("حلويات", 3), ("معجنات", 3),
        ("بروست", 3), ("برجر", 3), ("شاورما", 3), ("مشويات", 3), ("وجبات", 2), ("اكل", 1),
        ("مخبز", 3), ("عصائر", 2), ("مأكولات", 3), ("طبخ", 2), ("food", 2), ("ماكولات", 3),
        ("ضيافة", 2), ("قهوة", 2), ("تمور", 2), ("عسل", 2),
    ],
    "clothing": [
        ("ملابس", 3), ("ازياء", 3), ("أزياء", 3), ("عبايات", 3), ("عباية", 2), ("فساتين", 3),
        ("خياطة", 3), ("بوتيك", 3), ("boutique", 3), ("احذية", 3), ("أحذية", 3), ("شنط", 2),
        ("حقائب", 2), ("عطور", 3), ("perfume", 2), ("fashion", 3), ("موضة", 2), ("اطفال ملابس", 2),
        ("جينز", 2), ("قمصان", 2), ("مفارش", 2),
    ],
    "beauty": [
        ("تجميل", 3), ("مكياج", 3), ("ميك اب", 3), ("makeup", 3), ("بشرة", 2), ("عناية", 2),
        ("صالون", 3), ("salon", 3), ("حلاقة", 3), ("باربر", 3), ("barber", 3), ("سبا", 3),
        ("spa", 3), ("كوافير", 3), ("شعر", 1), ("عطر نسائي", 2), ("تسريحات", 2), ("اظافر", 2),
        ("رموش", 2), ("لاش", 2),
    ],
    "medical": [
        ("طبيب", 3), ("عيادة", 3), ("عيادات", 3), ("دكتور", 2), ("اسنان", 3), ("أسنان", 3),
        ("صيدلية", 3), ("مستشفى", 3), ("علاج", 2), ("طبية", 2), ("طبي", 2), ("clinic", 3),
        ("dental", 3), ("medical", 3), ("تغذية", 2), ("اخصائي", 2), ("فيزيو", 2),
        ("جلدية", 2), ("نفسي", 1), ("مركز طبي", 3),
    ],
    "education": [
        ("معلم", 2), ("معلمة", 2), ("دورات", 3), ("دورة", 2), ("تدريب", 3), ("كورس", 3),
        ("course", 3), ("training", 3), ("تعليم", 3), ("مدرس", 2), ("اكاديمية", 3),
        ("academy", 3), ("تأسيس طلاب", 3), ("حفظ القران", 2), ("قرآن", 1), ("لغة انجليزية", 2),
        ("تخرج", 1), ("جامعة", 2), ("مناهج", 2),
    ],
    "cleaning": [
        ("تنظيف", 3), ("نظافة", 3), ("cleaning", 3), ("مكافحة حشرات", 3), ("مكافحة", 2),
        ("عزل", 2), ("تعقيم", 3), ("رش مبيدات", 3), ("جلي بلاط", 3), ("تنظيف كنب", 3),
        ("تنظيف خزانات", 3), ("غسيل", 1),
    ],
    "furniture": [
        ("اثاث", 3), ("أثاث", 3), ("ديكور", 3), ("ديكورات", 3), ("مفروشات", 3), ("ستائر", 3),
        ("كنب", 3), ("furniture", 3), ("decor", 3), ("مطابخ", 3), ("خزائن", 2), ("انتريه", 2),
        ("غرف نوم", 2), ("سجاد", 2), ("جبس", 2), ("جبسون", 2),
    ],
    "contracting": [
        ("مقاولات", 3), ("مقاول", 3), ("بناء", 2), ("ترميم", 3), ("تشطيب", 3), ("تشطيبات", 3),
        ("بلاط", 2), ("سباكة", 3), ("كهرباء", 2), ("دهان", 2), ("لحام", 2), ("حداد", 2),
        ("عوازل", 2), ("خرسانة", 2), ("contracting", 3), ("construction", 3), ("مظلات", 3),
        ("سواتر", 3), ("هناجر", 2), ("ملاحق", 2),
    ],
    "printing": [
        ("طباعة", 3), ("مطبعة", 3), ("printing", 3), ("دعاية واعلان", 3), ("تغليف", 2),
        ("ستيكرات", 3), ("استيكرات", 3), ("لوحات", 2), ("بنرات", 3), ("كروت", 2),
        ("مطبوعات", 3), ("تصميم مطبوعات", 3), ("ليزر", 2),
    ],
    "tech": [
        ("كاشير", 3), ("برمجة", 3), ("تطبيقات", 3), ("تصميم مواقع", 3), ("مواقع", 2),
        ("جوالات", 3), ("كمبيوتر", 3), ("صيانة جوال", 3), ("الكترونيات", 3), ("إلكترونيات", 3),
        ("tech", 2), ("pos", 2), ("software", 2), ("برنامج", 2), ("انظمة", 2), ("كاميرات", 3),
        ("شبكات", 2), ("استضافة", 2), ("متجر الكتروني", 2),
    ],
    "finance": [
        ("تمويل", 3), ("تمويل شخصي", 3), ("قروض", 3), ("قرض", 2), ("محاسبة", 3), ("محاسب", 2),
        ("استثمار", 3), ("سجل تجاري", 3), ("تأسيس شركات", 3), ("تاسيس شركات", 3), ("زكاة", 2),
        ("ضريبة", 2), ("finance", 3), ("accounting", 3), ("تمويل عقاري", 2), ("اسهم", 2),
        ("تداول", 2), ("عملات", 2), ("تأسيس", 2), ("تاسيس", 2), ("رخصة تجارية", 3),
        ("start your business", 3), ("company formation", 3), ("business setup", 3),
        ("ownership", 2), ("رجال اعمال", 2),
    ],
    "travel": [
        ("سياحة", 3), ("سفر", 2), ("حجز طيران", 3), ("تذاكر", 3), ("فنادق", 3), ("رحلات", 3),
        ("عمرة", 3), ("حج", 2), ("travel", 3), ("tourism", 3), ("visa", 2), ("تأشيرات", 3),
        ("تاشيرات", 3), ("سياحية", 2), ("منظم رحلات", 3),
    ],
    "store": [
        ("متجر", 2), ("تسوق", 2), ("تجارة", 1), ("جملة", 2), ("بالجملة", 3), ("تخفيضات", 2),
        ("عروض", 1), ("store", 2), ("shop", 2), ("online store", 2), ("منتجات", 1),
        ("توريد", 2), ("مستلزمات", 2), ("هدايا", 2),
    ],
}


def _norm(s: str) -> str:
    s = s or ""
    # Normalize hamza/alef variants and taa-marbuta for matching.
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه")
    return s


def classify(bio: str, name: str = ""):
    """Classify an account's business type from its bio (+ name)."""
    text = _norm((bio or "") + " \n " + (name or "")).lower()
    scores = defaultdict(float)

    for key, kws in _KW.items():
        for kw, w in kws:
            if _norm(kw).lower() in text:
                scores[key] += w

    # We intentionally do NOT use the discovery source (search keyword) as a
    # signal: followers of an account in one category are not necessarily in the
    # same category, so relying on the bio is more accurate.

    if not scores:
        return {"category": None, "category_ar": None, "confidence": 0.0,
                "tags": [], "scores": {}}

    total = sum(scores.values())
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best = ranked[0][0]
    probs = {k: round(v / total, 3) for k, v in ranked}
    tags = [k for k, _ in ranked[:3]]
    return {
        "category": CATEGORIES[best][1],       # English label
        "category_ar": CATEGORIES[best][0],     # Arabic label
        "confidence": round(ranked[0][1] / total, 3),
        "tags": tags,                            # top-3 category keys
        "scores": probs,                         # probability distribution
    }


if __name__ == "__main__":
    tests = [
        ("Code Root | digital roots for your business growth", "Code Root"),
        ("مطعم لووك بروستد وجبات سريعه", "مطعم لوك"),
        ("للبيع والايجار شقق فلل اراضي مكتب عقاري بتبوك", "العقارية"),
        ("معرض سيارات وقطع غيار وتشليح", "سيارات"),
        ("توصيل طلبات وشحن دولي سريع", "دليفري"),
        ("عيادة اسنان وتجميل", "عيادة"),
    ]
    for b, n in tests:
        print(f"{n[:18]:18} -> {classify(b, n)}")
