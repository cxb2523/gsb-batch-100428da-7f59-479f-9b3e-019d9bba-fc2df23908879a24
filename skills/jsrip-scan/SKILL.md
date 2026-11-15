---
name: jsrip-scan
description: Use when user asks to scan a target for exposed JavaScript secrets, run a jsrip scan, analyze findings for true/false positives, or says /jsrip-scan. Single-target bug bounty recon — runs jsrip then auto-classifies every finding.
---

# jsrip-scan — Single-target JS secret recon with auto-review

## Overview

Run a `jsrip` scan against one URL, then analyze every finding against the actual JavaScript source to classify true positives vs false positives. Outputs `analyst_review.csv` and `REVIEW.md` inside the jsrip output directory.

**Project directory (the jsrip tool):** expected to be the current working directory, or `jsrip.py` on PATH. The skill checks and aborts clearly if jsrip isn't available.

**Two modes, auto-detected from the user's opening answer:**
- **Scan + review** — user gives a URL. Skill runs jsrip, then classifies.
- **Review-only** — user gives a path to an existing `jsrip_output_*` directory. Skill skips the scan and goes straight to classification.

## Phase 1 — Input

Ask the user **one** prompt (do not split into multiple turns):

> "Target URL, or path to an existing `jsrip_output_*` directory?
> Any overrides? (depth / max-pages / subdomains on-off / show-browser) — press enter for defaults."

Parse the user's response:

1. If the answer starts with `http://` or `https://`, **mode = scan**. Store the URL.
2. Else if the answer is a path that exists on disk and contains `reports/report.json`, **mode = review-only**. Store the directory path.
3. Else ask once for clarification: "I couldn't tell if that's a URL or an existing output dir. Paste the URL, or the full path to the `jsrip_output_*` directory."

**Defaults for scan mode** (used unless user overrode):

| Option             | Default                                           |
| ------------------ | ------------------------------------------------- |
| depth              | 3                                                 |
| max-pages          | 200                                               |
| --subdomains       | ON                                                |
| --include-external | OFF                                               |
| --show-browser     | OFF                                               |
| report format      | `-f json`                                         |
| output dir         | `./output/jsrip_output_<timestamp>/` (jsrip's default) |

Apply any overrides the user typed in their free-form response. If they explicitly asked to enable something off-by-default (e.g. `show-browser`), enable it.

## Phase 2 — Scan execution

**Skip this entire phase if mode = review-only.**

### Preflight

1. Check for `jsrip.py` in the current working directory. If missing, check `which jsrip` (installed on PATH). If neither is present, abort with:
   > "jsrip not found. Run `/jsrip-scan` from the jsrip project directory, or install it on your PATH."

2. If `jsrip.py` is local, check for a `venv/` alongside it. If it exists, prefer `./venv/bin/python jsrip.py` over bare `python3 jsrip.py`. If `venv/` is missing, proceed with `python3` but be ready to detect a missing-dependency stderr below.

### Run the scan

Construct the command. If `jsrip.py` is local, use `python3 jsrip.py`; otherwise use `jsrip`.

```
python3 jsrip.py -u <URL> \
  --depth <depth> \
  --max-pages <max-pages> \
  [--subdomains] \
  [--include-external] \
  [--show-browser] \
  -f json
```

Execute via the Bash tool. Stream output to the user (don't hide it — jsrip's progress logs are useful).

### After the scan

Locate the output directory: jsrip prints it on start. If you didn't capture it, find the most recently created `jsrip_output_*` directory under `output/`:

```
ls -dt output/jsrip_output_* | head -1
```

Store this path — it becomes the "output dir" for the rest of the skill.

### Exit-code handling

- **Exit 0 + `reports/report.json` exists** → proceed.
- **Non-zero exit + `reports/report.json` exists** → partial scan. Proceed, but note "partial scan (jsrip exited non-zero)" in the `REVIEW.md` header.
- **Non-zero exit + no `report.json`** → abort. Print the last 20 lines of jsrip's stderr. If the stderr contains `ModuleNotFoundError: No module named 'playwright'` (or any similar missing-module error), append this hint on a new line:
  > "Hint: the jsrip venv may not be set up. Run `bash setup.sh` in the jsrip project dir, then retry."

## Phase 3 — Parse findings and read JS context

### Load the report

Read `<output_dir>/reports/report.json`. Extract `findings.secrets` (a list).

**Zero findings?** Skip Phase 4. Jump to Phase 5 with an empty finding set — Phase 5 handles the empty case.

### Per-finding context gathering

For each finding, you already have a `context` field (~250 chars around the hit). For most findings that's enough.

**Read the full JS file when any of these are true:**
- `confidence == "high"` or the `type` matches a known CRITICAL pattern (see Phase 4) — full-file scan increases severity accuracy.
- The surrounding `context` includes words like `Authorization`, `Bearer`, `fetch(`, `XMLHttpRequest`, `axios` — these suggest live usage, worth verifying.
- The `context` appears truncated mid-token.

Full file path: `<output_dir>/javascript/<finding.file>`. If the file is missing (rare — happens if the finding came from an inline script whose path shifted), record `verdict = NEEDS REVIEW, explanation = "source file not found, cannot verify"` and skip Phase 4 for that finding.

Use the `Read` tool to pull the JS file. Since JS bundles are large, **read only the window you need:** ±40 lines around the hit if you can locate the line, else the first 500 lines.

### Locate the hit line in the file

The finding's `context` is a substring of the file. Grep the file for the finding's `value` (or a distinctive ~30-char snippet from `context`) to find the line number. Use the `Grep` tool with `-n: true`, `head_limit: 3`.

## Phase 4 — Classification heuristics

For each finding, assign **verdict** and, for TPs, **severity** (CRITICAL / HIGH / MEDIUM / LOW).

Verdict values are written to the CSV/MD in their **full form**: `TRUE POSITIVE`, `FALSE POSITIVE`, `NEEDS REVIEW`. The shorthand TP / FP below is for readability in this document only.

### FP signal table — if ANY match, verdict = FALSE POSITIVE

| Signal                                                   | Example                                                        |
| -------------------------------------------------------- | -------------------------------------------------------------- |
| ALL_CAPS error-constant identifier                       | `HOSTED_FIELDS_TOKENIZATION_NETWORK_ERROR` (Braintree)         |
| i18n / locale string value                               | `"Enter a password"`, `"Forgot password?"` inside a locale obj |
| CSS class name                                           | `"float-above"`, `"calendar-body-cell-preview"`                |
| localStorage / sessionStorage **key name** (not value)   | `localStorage.getItem("accessToken")`                          |
| Angular internal token                                   | `\u0275\u0275directiveInject`, `ɵɵdefineInjectable`            |
| MSAL error factory                                       | `createTokenRenewalTimeoutError()`, `createBadTokenError()`    |
| NgRx action type                                         | `"[USERS] LOAD RESET PASSWORD"`                                |
| Public key (reCAPTCHA site key, Stripe publishable)      | `pk_live_...` publishable, reCAPTCHA site key                  |
| Swagger UI OAuth label                                   | field labels in a Swagger authorize dialog                     |
| Microsoft login URL constant                             | `urlPIAEndAuth`, `urlPIAEndSignOut`                            |
| Variable name equals the matched value                   | `const accessToken = "accessToken"` (value is the key name)    |

### TP signal table — if ANY match and no FP signal, verdict = TRUE POSITIVE

| Signal                                                                                             | Example                                              | Severity hint |
| -------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ------------- |
| Known cloud-credential prefix                                                                      | `AKIA...`, `AIza...`, Azure AD `xxx~xxxxxx`, `ya29.` | **CRITICAL**  |
| DB connection string with embedded creds                                                           | `mongodb://user:pass@host`, `postgres://user:pass@…` | **CRITICAL**  |
| Stripe secret key                                                                                  | `sk_live_...`                                        | **CRITICAL**  |
| Slack / GitHub / webhook tokens                                                                    | `xoxb-...`, `ghp_...`, `whsec_...`                   | **HIGH**      |
| JWT with non-example payload                                                                       | `eyJhbGciOi...` decoded has real iss/sub             | **HIGH**      |
| Encryption key/IV adjacent to `encrypt(`, `decrypt(`, ciphertext literals                          | 32-byte hex passed to `createCipheriv`               | **HIGH**      |
| High-entropy string (>4.0 shannon) assigned to a suspicious name                                   | `apiKey = "aB3dE..."`                                | **MEDIUM** (→ HIGH if nearby HTTP usage) |
| Service-specific private key (Algolia admin, Mapbox secret, SendGrid)                              | provider-documented format                           | **MEDIUM**    |
| Low-impact tokens (analytics IDs that are secret but low-blast)                                    | e.g. Mixpanel server secret                          | **LOW**       |

### NEEDS REVIEW — verdict when

- No FP signal AND no TP signal.
- The matched value's context is genuinely ambiguous (e.g., a base64 blob that could be a secret, an asset hash, or a locale lookup key).
- Source file couldn't be read.

### Explanation field

For every finding, write a ≤120-char explanation stating which signal drove the verdict. Examples:
- TP: `"Azure AD client secret format (xxx~xxx) referenced in fetch() Authorization header at line 2847"`
- FP: `"ALL_CAPS Braintree error constant HOSTED_FIELDS_TOKENIZATION_NETWORK_ERROR — SDK internal"`
- NEEDS REVIEW: `"High-entropy base64 string, no nearby auth context, could be asset hash"`

## Phase 5 — Write outputs and summarize

Write two files into `<output_dir>/` (the same directory `reports/` lives in, NOT inside `reports/`).

### `analyst_review.csv`

Columns (this exact header, comma-separated, RFC 4180 quoting):

```
target,secret_type,file,line,verdict,severity,explanation
```

- `target` — the URL scanned (scan mode) or pulled from `scan_info.target_url` (review-only); empty string if not available.
- `line` — integer line number in the JS file if located, else empty.
- `verdict` — exactly one of `TRUE POSITIVE`, `FALSE POSITIVE`, `NEEDS REVIEW` (uppercase, with the space).
- `severity` — one of `CRITICAL|HIGH|MEDIUM|LOW` for `TRUE POSITIVE` rows, empty string for the other two verdicts.
- One row per finding. Header-only file if there are zero findings.

Use Python via the Bash tool to write this (handles quoting cleanly):

```python
import csv, json
with open('<output_dir>/analyst_review.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['target','secret_type','file','line','verdict','severity','explanation'])
    for row in rows:
        w.writerow(row)
```

### `REVIEW.md`

Use the `Write` tool. Structure:

```markdown
# Review — <target or "existing scan">

<ISO8601 timestamp>, <N> findings   [note "partial scan" if jsrip exited non-zero]

## True Positives

### CRITICAL
- **<secret_type>** — `<file>:<line>`
  ```
  <±5 lines of JS context around the hit>
  ```
  <explanation>

### HIGH
<same shape>

### MEDIUM
<same shape>

### LOW
<same shape>

## Needs Review

- **<secret_type>** — `<file>:<line>` — <explanation>

## False Positives

Collapsed by root-cause category — no per-finding listing:

- Braintree SDK error constants: 12
- i18n / locale strings: 7
- CSS class names: 3
- …
```

Omit severity sub-headers that have no entries (don't print empty `### LOW`).

**Empty-findings case:** write a one-line `REVIEW.md`:

```markdown
# Review — <target>

<timestamp>. No secrets detected in <js_files_count> JS files across <pages_count> pages.
```

Still write an `analyst_review.csv` with just the header row.

### Terminal summary

After writing both files, print:

```
Scan: <target>
Output: <output_dir>
Findings: <total>  →  TP: <tp_count> (<crit>/<high>/<med>/<low>)  FP: <fp_count>  Needs review: <nr_count>
Report: <output_dir>/REVIEW.md
```

If `tp_count` is zero, suffix the summary with: `No actionable secrets found.`

## Error handling

| Situation                                                      | Behavior                                                                                                             |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `jsrip.py` not in cwd and not on PATH                          | Abort before scan: "jsrip not found. Run `/jsrip-scan` from the jsrip project directory, or install it on your PATH." |
| Playwright not installed / browser fails at scan time          | Surface jsrip's stderr verbatim. No retry.                                                                            |
| jsrip exits non-zero + `reports/report.json` exists            | Proceed with classification. Add `(partial scan — jsrip exited non-zero)` to the `REVIEW.md` header.                  |
| jsrip exits non-zero + no `reports/report.json`                | Abort. Print last 20 lines of stderr. Do not write any review artifacts.                                              |
| Zero findings                                                  | Write header-only CSV + one-line `REVIEW.md`. Print summary.                                                          |
| Finding references a JS file that is missing                   | Verdict = `NEEDS REVIEW`, explanation = `"source file not found, cannot verify"`. Keep going.                         |
| Review-only mode: target path doesn't exist                    | Abort: `"Path does not exist: <path>."`                                                                               |
| Review-only mode: path exists but no `reports/report.json`     | Abort: `"No findings file at <path>/reports/report.json — was this a completed scan?"`                                |
| Findings count > 500                                           | Proceed sequentially. Do not parallelize.                                                                             |
| User aborts mid-scan (Ctrl-C on jsrip)                         | If `report.json` was written before the abort, treat as partial-scan case. Otherwise treat as no-report.json case.    |
