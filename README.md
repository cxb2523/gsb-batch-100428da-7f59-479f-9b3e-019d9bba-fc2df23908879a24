<p align="center">
  <img src="docs/hero.png" alt="crawlforge" width="100%">
</p>

# crawlforge

> An **open source alternative to Screaming Frog SEO Spider**, built LLM-native from day one.
> 269 technical SEO rules across 18 categories, native MCP server, DuckDB + Parquet storage, MIT licensed.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-309%20passing-brightgreen.svg)](#status)
[![Rules](https://img.shields.io/badge/SEO%20rules-269-violet.svg)](docs/RULES_CATALOG.md)
[![MCP](https://img.shields.io/badge/MCP-native-22D3EE.svg)](#using-it-from-claude-code--codex--cursor)

**Status: pre-alpha (May 2026).**

## What it does

`crawlforge` crawls a website and produces an opinionated technical SEO audit
backed by a queryable local database. If you have used Screaming Frog SEO
Spider, the workflow will feel familiar — same kinds of issues (titles, meta
descriptions, canonicals, hreflang, redirects, structured data, sitemaps,
images, mobile, AMP, accessibility…) — but with three architectural
differences explained below.

It is built around three ideas:

1. **One columnar source of truth.** Each crawl is a directory of Parquet
   files plus a DuckDB index. Run arbitrary SQL against the data afterwards,
   diff two crawls with a `JOIN`, ship audits to a data warehouse without an
   ETL.
2. **Rules as code.** The 269 detection rules are tiny, self-contained Python
   files with their own unit test. Adding a new rule is ~30 lines and a
   pytest fixture. The rule contract is documented in
   [`docs/RULES_API.md`](docs/RULES_API.md).
3. **MCP-first.** A Model Context Protocol server is shipped in the box, so
   Claude Code / Codex / Cursor can drive a crawl, query its results, summarise
   issues and write client-ready audits without any glue code.

There is no GUI. There is no SaaS. There is no telemetry.

## Stack

- **Python 3.11+** with `asyncio`
- **httpx** for the crawler (Playwright opt-in for JS rendering — Phase D)
- **lxml** for HTML extraction
- **advertools** for sitemap / robots.txt helpers
- **extruct** for structured-data extraction (JSON-LD, Microdata, RDFa)
- **datasketch** for MinHash + LSH near-duplicate detection
- **DuckDB** over **Parquet** for storage and analytical queries
- **FastMCP** for the MCP server
- **Typer + Rich** for the CLI

## Installation

```bash
git clone https://github.com/mario-hernandez/crawlforge.git
cd crawlforge
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## CLI usage

```bash
# Crawl a site (writes crawl.duckdb with all rules evaluated)
crawlforge run https://example.com -n 500 --db crawl.duckdb

# Issue counts by severity
crawlforge query crawl.duckdb "
  SELECT severity, COUNT(*) AS n FROM issues GROUP BY severity
"

# Top 20 firing rules
crawlforge query crawl.duckdb "
  SELECT rule_id, COUNT(*) AS n FROM issues
  GROUP BY rule_id ORDER BY n DESC LIMIT 20
"

# Critical issues with their URL
crawlforge query crawl.duckdb "
  SELECT i.rule_id, i.message, u.url
  FROM issues i LEFT JOIN urls u ON u.url_id = i.url_id
  WHERE severity = 'critical'
"

# Crawl metadata
crawlforge info crawl.duckdb

# List the 269 registered rules
crawlforge rules
```

## Using it from Claude Code / Codex / Cursor

Once registered, ask the assistant in plain language:

> *"Crawl example.com with crawlforge, max 200 URLs, then give me the top 10 critical issues with their URL."*
>
> *"From my last crawl, list every redirect chain longer than 2 hops."*
>
> *"Which hreflang rules fired? Group them by source URL."*

The MCP server exposes 8 tools: `crawl_run`, `list_crawls`, `query_urls`,
`list_issues`, `find_redirect_chains`, `find_orphans`, `audit_summary`,
`list_rules`.

### Manual registration

**Claude Code:**
```bash
claude mcp add crawlforge -s user -- /path/to/.venv/bin/python -m crawlforge.mcp
```

**Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.crawlforge]
command = "/path/to/.venv/bin/python"
args = ["-m", "crawlforge.mcp"]
```

## Data model

Each crawl populates 7 relational tables in a DuckDB file:

- `urls` — one row per URL, with extracted on-page metadata (title, meta, headings, canonical, hreflang, content hash, simhash, response headers, …)
- `links` — internal/external link graph with anchor, rel, position, and `in_navigation` / `in_footer` flags
- `redirects` — chains with hop count and loop detection
- `hreflang` — declarations from HTML / HTTP header / sitemap
- `structured_data` — extracted JSON-LD / Microdata / RDFa with validation flags
- `resources` — images, scripts, stylesheets referenced
- `issues` — output of the 269 rules: `rule_id`, `severity`, `category`, `evidence`, `message`

Full column reference: [`docs/SCHEMA.md`](docs/SCHEMA.md).

## Status

- **269 SEO rules** across 18 categories
- **309 tests passing**, 22 skipped (placeholders awaiting schema enrichment)
- ~18,000 lines of Python
- End-to-end smoke test verified: crawl → extract → persist → all 269 rules execute without crashing on a seeded site

## Roadmap

- [x] **Phase A** — Crawl engine + storage + Rule API + 4 pilot rules
- [x] **Phase B** — 269 SEO rules across 18 categories
- [x] **Phase C** — MCP server + full CLI
- [ ] **Phase D** — Playwright JS rendering · Performance & Accessibility rules (~123 more) · GSC / GA4 / PSI integrations · crawl diff · HTML reports · lightweight web UI
- [ ] **Phase E** — PyPI release · Homebrew tap · Docker image

Detail in [`EXECUTION_PLAN.md`](EXECUTION_PLAN.md). Full rule catalog in
[`docs/RULES_CATALOG.md`](docs/RULES_CATALOG.md).

## Coverage by category

| # | Category | Total | Active |
|---|---|---:|---:|
| 1 | Response Codes | 27 | 13 |
| 2 | Security | 14 | 9 |
| 3 | URL | 21 | 16 |
| 4 | Page Titles | 4 | 4 |
| 5 | Meta Description | 9 | 5 |
| 6 | Headings | 14 | 9 |
| 7 | Canonicals | 31 | 18 |
| 8 | Pagination | 7 | 2 |
| 9 | Hreflang | 33 | ~28 |
| 10 | Directives | 28 | ~14 |
| 11 | Sitemaps | 12 | 10 |
| 12 | Structured Data | 6 | 2 |
| 13 | Images | 9 | 5 |
| 14 | Mobile | 15 | 7 |
| 15 | AMP | 5 | 5 |
| 16 | Content | 15 | 5 |
| 17 | Links | 14 | 12 |
| 18 | Orphans | 5 | 4 |
| | **Total** | **269** | **~173** |

"Active" = `enabled_by_default = True`. The remaining rules ship as
placeholders that activate as the schema gains the underlying signals (JS
rendering, performance metrics, response headers, GSC/GA4 cross-data, …).

## Contributing

- Each rule lives in its own file under `src/crawlforge/rules/<category>/`
  with a unit test under `tests/unit/rules/<category>/`. See
  [`docs/RULES_API.md`](docs/RULES_API.md) for the contract.
- `pytest` from the repo root must stay green.
- Conventional commits welcome but not required.

## License

MIT — see [`LICENSE`](LICENSE).

## Trademarks & references

**Screaming Frog**® and **Screaming Frog SEO Spider**® are trademarks of
[Screaming Frog Ltd](https://www.screamingfrog.co.uk/) (UK, company no.
07277243). `crawlforge` is an **independent open source project**, not
affiliated with, endorsed by, or sponsored by Screaming Frog Ltd. References
to the product elsewhere in this document are nominative — they identify a
well-known piece of software in the same category so users can quickly
understand what `crawlforge` is for. No part of `crawlforge` is derived from
Screaming Frog's source code, configuration, or proprietary catalogs.

Other product, service, and company names are the property of their
respective owners and are referenced only for clarity.

The detection rules implemented here encode well-known, publicly documented
technical SEO best practices (HTTP semantics, indexing directives, Schema.org,
Core Web Vitals, WCAG, etc.). Each rule links to the relevant primary source —
typically Google Search Central, MDN Web Docs, web.dev, Schema.org, the W3C,
or sitemaps.org — so the underlying standard can be inspected directly.
