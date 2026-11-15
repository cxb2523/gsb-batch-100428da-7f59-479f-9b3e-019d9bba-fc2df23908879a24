# crawlforge — vision

## Premise

A technical SEO crawler is, at its core, three things stitched together:

1. An HTTP client that follows links.
2. A parser that extracts on-page signals.
3. A catalog of detection rules that flag deviations from well-known web
   standards (HTTP semantics, robots, canonicals, hreflang, Schema.org, Core
   Web Vitals, WCAG, sitemaps).

None of that is novel. The reason existing tools feel monolithic is not
algorithmic — it is the sheer volume of edge cases curated over many years.

The thesis behind `crawlforge`: with a clean rule contract and parallelisable
agents, that long tail can be re-encoded transparently in an MIT codebase
**without copying anyone's catalog**. Rules in this project are derived from
public web standards and primary documentation (Google Search Central, MDN,
web.dev, Schema.org, W3C, sitemaps.org) and re-expressed as small Python
modules.

## Three differentiating bets

### 1. MCP-native by default
The MCP server is not a wrapper around a CLI invocation. It speaks DuckDB
directly. Claude / Codex / Cursor can:

- launch a crawl (`crawl_run`)
- run arbitrary read-only SQL (`query_urls`)
- request prioritised issues (`list_issues`)
- diff two crawls (`diff_crawls`, Phase D)
- generate audits with client-ready copy (`audit_summary`)

### 2. Config as code
Audits are YAML files versionable in git, runnable in CI, reproducible:

```yaml
# audits/mysite.yml
name: Monthly site audit
url: https://example.com
max_urls: 100000
js_rendering:
  enabled: true
  match: ^https://example.com/products/.*
custom_extraction:
  - id: product_price
    selector: "span.price"
rules:
  enabled: ["*"]
  disabled: ["spelling_*"]
  thresholds:
    title_too_long: 65
output:
  parquet: ./crawls/mysite-{date}.parquet
  html_report: ./reports/mysite-{date}.html
```

### 3. Modern storage
DuckDB on Parquet:

- 1M+ URL crawls on an M-series Mac with <2 GB RAM
- analytical SQL queries in milliseconds
- columnar files are portable (move between machines, open in pandas / Polars
  / any SQL client)
- `JOIN` between two crawls = crawl diff

## Explicit non-goals

- **No GUI clone.** CLI + MCP + HTML reports cover the working flow. A web UI
  is optional for Phase D.
- **No native Windows support in MVP.** macOS / Linux first; Windows via WSL.
- **No managed SaaS.** `crawlforge` is and will remain a self-hosted MIT tool.
- **No bundled paid integrations.** If a user wants Ahrefs / Majestic / Moz
  data, they bring their own API key in their own script.

## Design principles

- **Composable over monolithic** — each rule is a file under 100 lines with
  its own test.
- **SQL over imperative** — most detections are clearer as DuckDB queries
  than as Python loops.
- **Fail loud** — if a rule cannot be evaluated (missing signal), it skips
  with a clear reason rather than silently returning empty.
- **Reproducible** — same input + same config → same output. No hidden state.
- **Honest about limits** — the README states what works and what does not,
  without marketing.
