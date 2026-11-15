# Subdomain Enumeration Feature - Design Spec

**Date:** 2026-04-07
**Status:** Approved

## Overview

Add a `--subdomains` flag to jsrip that performs passive + active subdomain enumeration before JS crawling. When enabled, jsrip extracts unique registrable domains from the input, discovers subdomains via crt.sh and DNS brute-force, probes them for liveness, prints a recon summary, and feeds alive subdomains into the existing crawl pipeline.

## Architecture

### Pipeline Change

```
[Existing]  Input → Crawl → Analyze → Report
[New]       Input → Enumerate → Recon Summary → Crawl → Analyze → Report
```

The enumeration stage is **additive** - it only activates when `--subdomains` is passed. Without it, the tool behaves identically to today.

### New File: `core/enumerator.py`

Single new module following the existing pattern of one class per concern in `core/`.

```python
@dataclass
class SubdomainResult:
    subdomain: str        # e.g. "api.target.com"
    ip: str               # resolved IP address
    status_code: int      # HTTP status code (0 if unreachable)
    alive: bool           # responded to HTTP probe
    source: str           # "crt.sh" | "bruteforce" | "both"

class SubdomainEnumerator:
    def __init__(self, domains: set, wordlist_path: str = None,
                 dns_concurrency: int = 50, http_concurrency: int = 20):
        ...

    async def enumerate(self) -> dict[str, list[SubdomainResult]]:
        """Run full enumeration pipeline for each domain.
        Returns {domain: [SubdomainResult, ...]}"""
        ...
```

### Three Internal Phases

**1. Certificate Transparency - `_query_crtsh(domain)`**
- GET `https://crt.sh/?q=%.{domain}&output=json`
- Parse response, extract unique subdomain names
- Deduplicate wildcards (`*.target.com` → skip)
- Timeout: 15s. Graceful failure if crt.sh is down (log warning, continue)

**2. DNS Brute-force - `_bruteforce_dns(domain)`**
- Load wordlist: custom path via `--wordlist` or built-in `wordlists/subdomains.txt`
- Resolve `{word}.{domain}` using `aiodns` async resolver
- Semaphore-controlled concurrency (default: 50 concurrent lookups)
- Collect resolved subdomains + IP addresses
- Skip NXDOMAIN / timeout silently

**3. HTTP Alive Check - `_check_alive(subdomains)`**
- Merge results from phase 1 + 2, deduplicate by subdomain name
- HTTP GET `https://{subdomain}` with fallback to `http://{subdomain}`
- `aiohttp` with concurrency cap of 20, timeout 10s per request
- Record status code, mark `alive=True` if any response received
- Follow redirects (max 3) but record final status code

### Domain Extraction

New helper in `jsrip.py`:

```python
def extract_domains(urls: list) -> set:
    """Extract unique registrable domains from any input format.

    Handles:
      https://app.target.com/path  → target.com
      http://target.com            → target.com
      api.target.com               → target.com
      target.com                   → target.com
    """
```

Uses `tldextract` (already available). Deduplicates so enumeration runs once per unique registrable domain regardless of how many input URLs share it.

## CLI Interface

### New Flags

```
Subdomain Enumeration:
  --subdomains            Enable subdomain enumeration before JS crawling
  --wordlist PATH         Custom wordlist for DNS brute-force
                          (default: built-in ~5-10k list)
```

Added as a new argument group in `parse_arguments()`.

### Orchestration in `main()`

```python
if args.subdomains:
    domains = extract_domains(urls)
    enumerator = SubdomainEnumerator(domains, wordlist_path=args.wordlist)
    recon = await enumerator.enumerate()
    print_recon_summary(recon)
    save_recon_report(recon, output_dir)
    alive_urls = [f"https://{r.subdomain}" for results in recon.values()
                  for r in results if r.alive]
    urls = list(set(urls + alive_urls))

# existing pipeline continues with enriched url list
```

Original input URLs are always preserved. Discovered subdomains are additive.

## Output

### Terminal Recon Summary

Printed before crawling begins:

```
[*] Subdomain Enumeration Results
[*] ================================
[*] target.com - 47 subdomains found, 31 alive
[*]   Sources: crt.sh (38), bruteforce (22), both (13)
[*]
[*]   ALIVE:
[*]     api.target.com        → 104.21.5.12    [200]
[*]     staging.target.com    → 104.21.5.13    [302]
[*]     admin.target.com      → 104.21.5.14    [403]
[*]     dev.target.com        → 104.21.5.15    [200]
[*]     ...
[*]   DEAD (resolved but no HTTP):
[*]     old.target.com        → 104.21.5.20
[*]     legacy.target.com     → 104.21.5.21
[*]
[*] Proceeding to JS crawl with 31 alive subdomains + 2 original URLs
```

### File Output

**`reports/subdomains.json`** - standalone recon report:

```json
{
  "target.com": {
    "total": 47,
    "alive": 31,
    "wildcard_filtered": 9,
    "unresolved": 0,
    "results": [
      {
        "subdomain": "api.target.com",
        "ip": "104.21.5.12",
        "status_code": 200,
        "alive": true,
        "source": "both",
        "classification": "alive",
        "filter_reason": ""
      },
      {
        "subdomain": "random1.target.com",
        "ip": "104.21.5.12",
        "status_code": 0,
        "alive": false,
        "source": "crt.sh",
        "classification": "wildcard",
        "filter_reason": "resolved to wildcard IP 104.21.5.12"
      }
    ]
  }
}
```

Wildcard filtering applies to CT-discovered names as well as brute-force
results: the wildcard probe's IP set is compared against every resolved
host. A match means the name is never HTTP-probed or counted alive, but the
record stays in the report — `classification` separates `alive` / `dead` /
`wildcard` / `unresolved` so a wildcard domain's output is auditable.

**`reports/report.json`** - main report gains a `"subdomain_recon"` top-level key containing the same data.

**`reports/report.html`** - gains a "Subdomains" tab showing the recon data when `--subdomains` was used.

## Built-in Wordlist

**File:** `wordlists/subdomains.txt`

~5-10k entries organized by category:

```
# Common
www, api, app, mail, dev, staging, admin, test, beta, portal, dashboard

# Cloud/Infrastructure
s3, cdn, k8s, aws, gcp, azure, cf, lb, proxy, gateway, vpn

# Services
grafana, jenkins, gitlab, jira, sentry, kibana, elastic, prometheus, vault

# Environments
dev, stage, staging, prod, production, uat, qa, sandbox, demo, preprod

# Other patterns
internal, private, corp, intranet, extranet, partner, vendor, docs, wiki
```

Shipped in the repo. `--wordlist` overrides it entirely (does not merge).

## Dependencies

**New:** `aiodns>=3.0.0` - async DNS resolver for brute-force phase.

**Existing (already in requirements.txt):**
- `aiohttp` - used for crt.sh queries and HTTP alive-check
- `tldextract` - used for domain extraction (already used in crawler.py)

No other new dependencies.

## Scope Boundaries

**In scope:**
- `core/enumerator.py` - new module
- `wordlists/subdomains.txt` - built-in wordlist
- `jsrip.py` - new flags, domain extraction helper, orchestration logic
- `utils/reporter.py` - subdomain recon in JSON/HTML reports
- `requirements.txt` - add aiodns

**Out of scope:**
- API key integrations (SecurityTrails, VirusTotal, etc.)
- Subdomain takeover detection
- Port scanning
- Recursive subdomain enumeration (enum subdomains of subdomains)

## Error Handling

- crt.sh timeout/failure: log warning, continue with brute-force results only
- DNS resolver failure: log warning, skip that word, continue
- HTTP probe timeout: mark subdomain as dead, continue
- Empty results: log info message, proceed with original URLs only
- Invalid wordlist path: exit with clear error message before starting
