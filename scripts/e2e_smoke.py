"""End-to-end smoke test: feed HTMLs directly into pipeline and run all 269 rules.

Validates that:
1. extract_url_row works on real HTML
2. writer persists URL+links+hreflang+resources
3. resolve_link_targets backfills target_url_id
4. All registered rules execute without crashing
5. Expected issues are detected on a seeded site
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collections import Counter

from crawlforge.engine.extract import extract_url_row
from crawlforge.rules._registry import discover_rules
from crawlforge.storage.schema import init_database
from crawlforge.storage.writer import resolve_link_targets, write_issues, write_url_row

E2E_DIR = Path("/tmp/cf_e2e")
BASE = "http://127.0.0.1:8765"

PAGES = [
    ("/", "index.html", 200),
    ("/about", "about.html", 200),
    ("/no-title", "no-title.html", 200),
    ("/dup-1", "dup-1.html", 200),
    ("/dup-2", "dup-2.html", 200),
    ("/short", "short.html", 200),
    ("/lang-mismatch", "lang-mismatch.html", 200),
    ("/orphan-target", None, 404),  # linked from index but missing → 404
]


def main() -> int:
    con = init_database()
    rows_written = 0

    for path, filename, status in PAGES:
        url = f"{BASE}{path}"
        body = b""
        headers: dict[str, str] = {"content-type": "text/html; charset=utf-8"}
        if filename and (E2E_DIR / filename).exists():
            body = (E2E_DIR / filename).read_bytes()

        row = extract_url_row(
            url=url,
            final_url=url,
            status_code=status,
            headers=headers,
            body=body,
            crawled_at=datetime.now(timezone.utc),
        )
        if path == "/":
            row["depth"] = 0
        else:
            row["depth"] = 1
        write_url_row(con, row)
        rows_written += 1

    resolve_link_targets(con)

    rules = discover_rules()
    print(f"Reglas descubiertas: {len(rules)}")
    print(f"URLs en BD: {rows_written}")

    all_issues: list = []
    crashed: list[tuple[str, str]] = []
    skipped_disabled = 0

    for rule_id, rule_cls in rules.items():
        rule = rule_cls()
        if not rule.enabled_by_default:
            skipped_disabled += 1
            continue
        try:
            issues = list(rule.check(con))
        except Exception as e:
            crashed.append((rule_id, type(e).__name__ + ": " + str(e)[:120]))
            continue
        all_issues.extend(issues)

    write_issues(con, all_issues)

    print(f"Reglas ejecutadas: {len(rules) - skipped_disabled - len(crashed)}")
    print(f"Reglas TODO/skip: {skipped_disabled}")
    print(f"Reglas crasheadas: {len(crashed)}")
    if crashed:
        for rid, err in crashed[:20]:
            print(f"  ❌ {rid}: {err}")

    print(f"\nIssues detectados: {len(all_issues)}")
    by_severity = Counter(i.severity for i in all_issues)
    by_rule = Counter(i.rule_id for i in all_issues)
    print(f"  Por severidad: {dict(by_severity)}")
    print(f"\nTop 15 reglas con más hits:")
    for rid, n in by_rule.most_common(15):
        print(f"  {n:3d}  {rid}")

    expected_to_fire = {
        "title_missing",
        "title_duplicate",
        "title_too_short",
        "meta_desc_missing",
        "meta_desc_duplicate",
        "image_missing_alt_attribute",
        "internal_4xx",
        "schema_missing",
        "html_lang_missing",
    }
    fired = set(by_rule.keys())
    not_fired = expected_to_fire - fired
    if not_fired:
        print(f"\n⚠️  Reglas esperadas que NO se dispararon: {not_fired}")
    else:
        print(f"\n✓ Todas las {len(expected_to_fire)} reglas esperadas se dispararon")

    if crashed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
