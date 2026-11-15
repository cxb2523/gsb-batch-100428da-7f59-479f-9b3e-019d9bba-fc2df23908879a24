# Batch pipeline

Notes on scaling jsrip across a large engagement scope.

jsrip itself is single-run. For engagements with a large in-scope list (hundreds of URLs across hundreds of apps), the shape that works is a thin wrapper around it. **No such wrapper ships with this repo** — scope CSVs and their parsing rules are engagement-specific, so build your own. What it needs to do:

**1. Target extraction.** Read the scope CSV, filter rows against your scope keywords, pull URLs out of however many environment columns you have (DAST / UAT / Prod), clean messy cells, validate each hostname has a real TLD, dedupe, and keep per-app metadata (app ID, name, region, owner) attached to each URL.

**2. Grouped execution.** Group targets by app rather than by URL, so one logical app is one scan with one output directory. Then:

- **Parallelism with a per-app timeout** — a single hanging target shouldn't stall the run.
- **Per-app isolation** — give each app its own `output/scans/<app>/{javascript/, reports/, urls.txt}`.
- **Crash-safe output** — stream findings to CSV incrementally instead of writing once at the end.
- **Resumability** — checkpoint completed apps to a progress file so an interrupted run picks up where it stopped.
- **Metadata join** — carry app ID, name, region, owner, and environment back onto every finding row.
- **Selective subdomain enum** — run `--subdomains` on specific app domains only. Pointing it at a large shared parent domain returns thousands of hosts that have nothing to do with your target.

---

[← back to the README](../README.md)
