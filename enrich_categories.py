#!/usr/bin/env python3
"""
Enrich a results file by classifying each account's business type (using
categories.py). Adds the fields: category, category_ar, category_confidence,
category_tags. Prints a per-category summary.

Usage:
    python3 enrich_categories.py                 # default file output/tiktok-bio.json
    python3 enrich_categories.py path/to/file.json
"""

import sys
import os
import json
import collections

import categories

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.normpath(os.path.join(HERE, "output/tiktok-bio.json"))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    by_cat = collections.Counter()
    for r in data:
        c = categories.classify(r.get("bio", ""), r.get("name", ""))
        r["category"] = c["category"]
        r["category_ar"] = c["category_ar"]
        r["category_confidence"] = c["confidence"]
        r["category_tags"] = c["tags"]
        by_cat[c["category"] or "Unclassified"] += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[done] classified {len(data)} record(s) in:\n   {path}\n")
    print("Distribution by category:")
    for name, n in by_cat.most_common():
        print(f"  {name:28} {n}")


if __name__ == "__main__":
    main()
