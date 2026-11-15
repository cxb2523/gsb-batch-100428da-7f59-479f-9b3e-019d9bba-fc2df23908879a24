# `/jsrip-scan` — Claude Code integration

jsrip ships with a user-level skill that runs a single-target scan and auto-classifies every finding against the actual JavaScript source. Designed for bug-bounty recon where you want triage, not raw output.

## Install the skill (one-time)

```bash
mkdir -p ~/.claude/skills/jsrip-scan
cp skills/jsrip-scan/SKILL.md ~/.claude/skills/jsrip-scan/SKILL.md
```

Claude Code picks it up automatically — no reload required. Verify with `/jsrip-scan` in any Claude Code session.


## Exact steps to use it

**1. Start Claude Code in the jsrip project root.** The skill aborts if it can't find `jsrip.py` in the current working directory (fallback: `jsrip` on PATH).

```bash
cd ~/jsrip
claude
```

**2. Invoke:**

```
/jsrip-scan
```

**3. Answer the single prompt.** The skill asks:

> Target URL, or path to an existing `jsrip_output_*` directory?
> Any overrides? (depth / max-pages / subdomains on-off / show-browser) — press enter for defaults.

Two modes, auto-detected from your answer:

| Your answer                                                 | Mode          | What happens                                       |
| ----------------------------------------------------------- | ------------- | -------------------------------------------------- |
| `https://target.com`                                        | scan + review | jsrip runs, then classifies                        |
| `/path/to/output/jsrip_output_20260420_154412/`             | review-only   | skips the scan, classifies existing findings       |

Free-form overrides work in the same line: `https://target.com depth 4 show-browser`. Scan-mode defaults if you just press enter: `--depth 3`, `--max-pages 200`, `--subdomains on`, `--show-browser off`, `-f json`.

**4. Let the scan run.** jsrip's progress logs stream live. Output dir lands at `./output/jsrip_output_<timestamp>/` unless you overrode.

**5. Classification phase.** For each finding the skill:

  a. Reads the ~250-char `context` from `reports/report.json`.
  b. If the pattern is known-critical or the context mentions `Authorization` / `Bearer` / `fetch(` / `axios` / `XMLHttpRequest`, it opens the JS file at `<output_dir>/javascript/<file>` and reads ±40 lines around the hit.
  c. **FP table first** — matches against a table of known false positives: ALL_CAPS error constants (Braintree, MSAL), i18n strings, CSS class names, storage key *names* (not values), Angular internals (`ɵɵ*`), MSAL error factories, NgRx action types, public keys (reCAPTCHA, Stripe publishable), Microsoft OAuth2 endpoint URLs, etc.
  d. **TP table second** — if no FP signal fires: known cloud prefixes (`AKIA`, `AIza`, Azure AD `xxx~xxxxx`, `ya29.`), DB connection strings with embedded creds, `sk_live_`, Slack / GitHub / webhook tokens, JWTs with non-example payloads, encryption keys adjacent to `encrypt(` / `decrypt(`, service-specific private keys.
  e. **Severity** (for TPs only): CRITICAL / HIGH / MEDIUM / LOW.
  f. **NEEDS REVIEW** when neither table fires or the source file can't be read.

**6. Outputs land next to `reports/`** (not inside it):

- **`analyst_review.csv`** — one row per finding:
  ```
  target,secret_type,file,line,verdict,severity,explanation
  ```
  Verdicts are exactly `TRUE POSITIVE` / `FALSE POSITIVE` / `NEEDS REVIEW`. `severity` is filled only for TPs.
- **`REVIEW.md`** — TPs grouped by severity with ±5 lines of JS around each hit, Needs Review bullets, and FPs bucketed by root-cause category (counts only, no per-finding noise).

**7. Terminal summary:**

```
Scan: https://target.com
Output: output/jsrip_output_20260420_154412/
Findings: 34  →  TP: 2 (1/1/0/0)  FP: 27  Needs review: 5
Report: output/jsrip_output_20260420_154412/REVIEW.md
```

## Review-only mode (re-triage an older scan)

Got a `jsrip_output_*` directory from an earlier run and want classification without rescanning?

```
/jsrip-scan
```

Answer with the absolute path. The skill verifies `reports/report.json` exists, then jumps straight to classification. CSV + MD land in the same place.

## When the skill aborts

| Situation                                                 | What the skill does                                                                              |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Neither `jsrip.py` in cwd nor `jsrip` on PATH             | Aborts before any scan. `"jsrip not found. Run /jsrip-scan from the jsrip project directory."`   |
| jsrip exits non-zero, no `reports/report.json`            | Aborts. Prints last 20 lines of stderr. If Playwright is missing, hints to run `bash setup.sh`.  |
| jsrip exits non-zero, `reports/report.json` exists        | Proceeds. `REVIEW.md` header is marked `(partial scan — jsrip exited non-zero)`.                 |
| Zero findings                                             | Header-only CSV and one-line `REVIEW.md`. No padding.                                            |
| Review-only path doesn't exist                            | Aborts: `"Path does not exist: <path>."`                                                         |
| Review-only path has no `reports/report.json`             | Aborts: `"No findings file at <path>/reports/report.json — was this a completed scan?"`          |
| Finding references a JS file that's missing on disk       | Keeps going. That row is `NEEDS REVIEW` with explanation `"source file not found, cannot verify"`. |

---

[← back to the README](../README.md)
