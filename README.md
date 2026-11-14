# webxray

Offensive web scanner: crawling, XSS, SQLi, headers and WAF bypass.

> 🇪🇸 [Versión en español](README.es.md)

---

## What does it do?

Offensive web scanner written in Python that combines crawling, XSS detection, SQL injection, security header analysis and WAF detection.

---

## Features

- Application crawling with a traceable outcome per URL (`--crawl-report`)
- Reflected XSS detection (GET and forms)
- SQLi detection via GET and POST forms
- Security header analysis
- WAF detection and bypass (`--waf-xss`)
- Custom headers (`-H`), cookies (`-c`), proxy (`-x`, Burp), `-k` and `--rate` req/s limit
- JSON / JSONL output

---

## Installation

```bash
git clone https://github.com/theoffsecgirl/webxray.git
cd webxray
pip install -e .
```

---

## Usage

```bash
webxray -u https://example.com

# Bug bounty: identifying header, authenticated session, rate limit
webxray -u https://target.com -H "X-Bugbounty: myuser" -c "session=..." --rate 5

# Through Burp
webxray -u https://target.com -x http://127.0.0.1:8080 -k
```

### Pipeline

```bash
webxray -u https://target.com --format jsonl --stdout | jq 'select(.severity=="high")'
```

> `-H`, `-c`, `-x`, `-k` and `--rate` apply to every request webxray sends (crawler included). In `--waf-xss` mode, WAF detection is done by `wafw00f`, which does not receive them.

### Discovery outcomes

Every candidate URL the crawler touches gets a stable outcome record; failed pages no longer abort the round and are no longer skipped silently. Outcomes:

| outcome | meaning |
| --- | --- |
| `fetched` | requested, response received and HTML parsed |
| `parse_failed` | response received but the body could not be parsed |
| `depth_limit` | enqueued but not requested because `--depth` was reached |
| `request_failed` | the HTTP request failed (see the `error` field) |
| `excluded_cross_site` | off-site link, excluded from the crawl |
| `duplicate` | URL already seen, ignored |

Each record always has the same seven fields: `url`, `outcome`, `depth`, `source`, `status`, `error`, `links_found`. They are written to a separate document with `--crawl-report` (JSON/JSONL, honoring `--format`) and never mixed into the findings stream, so existing `--json-output`/`--stdout` consumers keep the same schema. The default scan still covers every enqueued URL — including failed and depth-limited ones — via `scannable_urls()`.

```bash
webxray -u https://target.com --crawl-report crawl.jsonl --format jsonl
```

---

## Notes

- Findings are candidates, not confirmed vulnerabilities
- Designed for bug bounty recon and pipeline integration

---

## License

MIT
