#!/usr/bin/env python3
"""
Enrich a results file by adding a country guess + probabilities to each record
(using country.py). Adds the fields: country, country_ar, country_iso, dial_code,
country_confidence, country_probs. Prints a per-country summary.

Usage:
    python3 enrich_country.py                 # default file output/tiktok-bio.json
    python3 enrich_country.py path/to/file.json
"""

import sys
import os
import json
import collections

import country

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.normpath(os.path.join(HERE, "output/tiktok-bio.json"))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    by_country = collections.Counter()
    low_conf = 0
    for r in data:
        c = country.detect_country(r.get("phone", ""), r.get("bio", ""))
        r["country"] = c["country"]
        r["country_ar"] = c["country_ar"]
        r["country_iso"] = c["iso"]
        r["dial_code"] = c["dial_code"]
        r["country_confidence"] = c["confidence"]
        r["country_probs"] = c["probabilities"]
        by_country[c["country"] or "Unknown"] += 1
        if c["confidence"] and c["confidence"] < 0.6:
            low_conf += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[done] enriched {len(data)} record(s) in:\n   {path}\n")
    print("Distribution by country (most likely):")
    for name, n in by_country.most_common():
        print(f"  {name:22} {n}")
    print(f"\nRecords with confidence < 0.6 (need verification): {low_conf}")


if __name__ == "__main__":
    main()
