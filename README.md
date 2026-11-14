# Web Source Code Downloader & Crawler

<div align="right">

[![Sponsor SagarBiswas-MultiHAT on Polar.sh](https://img.shields.io/badge/Sponsor-Polar.sh-0062FF?style=for-the-badge&logo=githubsponsors&logoColor=white)](https://buy.polar.sh/polar_cl_I7DT5UQNUs1Qjb4VrFYOiRA2nkb3RVVUJCnjr3o5SGK)<br>
[![Actions Status](https://github.com/SagarBiswas-MultiHAT/WebSource-Harvester/actions/workflows/github-actions.yml/badge.svg)](https://github.com/SagarBiswas-MultiHAT/WebSource-Harvester/actions) 
[![License](https://img.shields.io/github/license/SagarBiswas-MultiHAT/WebSource-Harvester.svg)](https://github.com/SagarBiswas-MultiHAT/WebSource-Harvester/blob/main/LICENSE) 
[![Last Commit](https://img.shields.io/github/last-commit/SagarBiswas-MultiHAT/WebSource-Harvester.svg)](https://github.com/SagarBiswas-MultiHAT/WebSource-Harvester/commits) 
[![Open Issues](https://img.shields.io/github/issues/SagarBiswas-MultiHAT/WebSource-Harvester.svg)](https://github.com/SagarBiswas-MultiHAT/WebSource-Harvester/issues) 
[![Top Language](https://img.shields.io/github/languages/top/SagarBiswas-MultiHAT/WebSource-Harvester.svg)](https://github.com/SagarBiswas-MultiHAT/WebSource-Harvester)
![Release](https://img.shields.io/github/v/release/SagarBiswas-MultiHAT/WebSource-Harvester)

</div>

**Author:** @SagarBiswas-MultiHAT \
**Category:** Educational Web Crawling & Client-Side Security Analysis \
**Status:** Learning-grade, interview-safe, portfolio-ready

> “This project performs depth-controlled crawling and client-side source reconstruction, capturing everything a browser can observe from a given URL, while intentionally respecting server-side trust boundaries.”

---

![after running the project](https://imgur.com/luk0aqV.png)

---

#### Tested example: python ".\PasourceDownloader.pyssword-Strength-Checker" https://sagarbiswas-multihat.github.io/ --depth 2

<br>

<div align="center">
    
![downloaded elements](https://imgur.com/8yCIRkQ.png)

</div>

<br>

---

## Overview

This project is an **educational website source code downloader and crawler** that extracts and reconstructs **everything a browser can observe** from a given URL.

It crawls a site with depth-controlled **BFS**, downloads client-visible resources (HTML, CSS, JS, images, fonts, PDFs, etc.), and rewrites links so the pages work **offline**, even on nested paths like `/blog/*`.

The tool **respects server trust boundaries** and does **not** attempt to fetch backend code, databases, or private data.

---

## What this project provides (accurate scope)

This tool captures **everything a browser can retrieve** from a URL:

- HTML pages (multiple pages via crawling)
- Linked CSS files
- JavaScript files
- Images (including `srcset`)
- Fonts and media files
- PDFs and other static assets
- XML files (e.g., `sitemap.xml`)
- Correct offline reconstruction via path rewriting

Ideal for:

- Learning how real websites are structured
- Offline inspection and analysis
- Client-side security research
- Understanding exposure and attack surface
- Portfolio demonstrations of crawling logic

---

## What this project intentionally does NOT do

By design, this project does **not**:

- Download backend source code (PHP, Python, Node.js, etc.)
- Access databases or APIs that require authentication
- Execute JavaScript (SPA/React/Vue rendering)
- Bypass authentication, paywalls, or access controls
- Retrieve secrets, tokens, or server configuration

These limitations are **intentional** and make the project accurate and interview-safe.

---

## Key features

- **Depth-controlled crawling** (`--depth 2` or `--depth 1-2`)
- **Breadth-First Search (BFS)** for reliable depth measurement
- **Crawl vs analyze separation** to control what gets saved
- **Same-origin enforcement** (no external domain crawling)
- **Offline-safe path rewriting** for nested pages
- **Asset handling** for `src`, `href`, and `srcset`
- **URL decoding** (`%20` → spaces)
- **Query collision handling** via hash suffix
- **Content-type aware saving** for missing extensions
- **XML-aware parsing** for sitemaps and RSS
- **Export visited URLs** (`--export-urls`) to a text file for analysis
- **Per-page failure manifest** (`failures.txt`) listing every resource or page that could not be saved, with its original URL, owning page, error category and counts — thread-safe under `--threads`

---

## How depth works (`--depth`)

Depth is measured in **link hops** from the base URL:

- `0` → only the base URL
- `1` → base URL + pages directly linked from it
- `2` → links from depth-1 pages
- `1-2` → crawl broadly, analyze only depth 1–2

Example structure:

```
Depth 0
└── https://example.com

Depth 1
├── /about
├── /blog
└── /login

Depth 2
├── /blog/post-1
├── /blog/post-2
└── /about/team
```

Depth control reduces noise and focuses on pages where real-world issues usually live.

---

## Installation

Recommended: use a virtual environment.

```bash
python -m venv .venv
```

Activate:

**Windows (PowerShell):**

```bash
.venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
source .venv/bin/activate
```

Install dependencies:

1. Install from the bundled `requirements.txt` (recommended):

```bash
pip install -r requirements.txt
```

2. Or install packages individually (equivalent):

```bash
pip install beautifulsoup4 lxml
```

Notes:

- `lxml` is optional but recommended (it provides a robust XML parser and removes XML parsing warnings).
- Use the Python provided in your `.venv` when running the `pip` command to ensure packages install into the virtual environment.

---

## Usage

```bash
python sourceDownloader.py <BASE_URL> --depth <DEPTH> [--export-urls]
```

### Options

| Option | Description |
|--------|-------------|
| `url` | Base URL to crawl (include http:// or https://) |
| `--depth` | Crawl depth (e.g., `2` or `1-2`). Default: `0` |
| `--threads` | Number of threads for concurrent crawling. Default: `1` |
| `--no-download` | Only crawl and export URLs, skip downloading files |
| `--export-urls` | Export all visited URLs to `urls.txt` in the output directory |

### Examples

```bash
# Crawl only the base URL
python sourceDownloader.py https://example.com --depth 0

# Crawl base URL and pages directly linked from it
python sourceDownloader.py https://example.com --depth 1

# Crawl depth 1-2, analyze pages at depth 1 and 2
python sourceDownloader.py https://example.com --depth 1-2

# Crawl and export all visited URLs to urls.txt
python sourceDownloader.py https://example.com --depth 2 --export-urls
```

---

## Output structure (example)

```
example_com/
├── index.html
├── assets/
│   ├── css/
│   ├── js/
│   ├── images/
│   └── fonts/
├── blog/
│   ├── post-1.html
│   └── post-2.html
├── failures.txt      (only when at least one resource or page failed)
└── urls.txt          (when using --export-urls)
```

All pages open **offline** without broken CSS or images.

---

## Why nested pages work correctly

Many crawlers rewrite assets relative to the project root, which breaks pages like `/blog/post.html`.

This project rewrites assets **relative to each HTML file’s directory**, so both root and nested pages load properly.

---

## Output behavior (important)

- **Assets are saved as binary**, so images and PDFs stay intact.
- **Pages are saved as HTML**, with rewritten local paths.
- **External URLs** (GitHub badges, CDNs) are kept external.
- **Fragment-only links** (`#about`) are ignored to reduce crawl noise.

---

## Failure manifest (`failures.txt`)

When an object cannot be saved, the crawl continues instead of aborting the
page, and every failure is recorded so nothing is silently lost.

- **Resource failure** (HTTP error, timeout or connection failure while
  downloading an image/CSS/JS/...): the original URL, the page that referenced
  it and the error category are recorded. The remaining resources on that page
  are still processed, and the page itself is still saved (the broken reference
  is left untouched, successful references are still rewritten to relative paths).
- **Page failure** (the page's own request failed): the page is recorded and
  **no local file is written for it**.

Error categories: `http-error` (with the HTTP status, e.g. `HTTP 404`),
`timeout`, `connection-error`, and a generic `download-error` fallback.

At the end of a run, if at least one failure occurred a `failures.txt` file is
written into the output directory. Failures are **grouped by page**, each group
shows its resource/page counts, and the header gives the totals. The exact same
content is printed to the terminal. When every object succeeds, **no manifest is
created** (no empty file).

```
Failure report
Total failed resources: 2
Total failed pages: 1

Page: http://example.com/
  Failed resources: 1 | Failed page entries: 0
  [resource] http://example.com/assets/missing.png -> http-error (HTTP 404)

Page: http://example.com/missing-page.html
  Failed resources: 0 | Failed page entries: 1
  [page] http-error (HTTP 404)
```

The ledger is shared and lock-protected, so with `--threads > 1` each failure is
still attributed to the page that owned the request, with no cross-page mixing
and no double counting.

A self-contained verification harness for this behavior lives in
`tests/verify_failures.py`: it builds a local static site containing a missing
image, a 404 page link, a connection-refused asset and a slow (timeout) asset,
then runs the crawler single-threaded and multi-threaded and asserts identical,
correctly-attributed manifests.

```bash
python tests/verify_failures.py
```

---

## Troubleshooting

- **Images missing on nested pages** → fixed by file-relative rewriting
- **Responsive images missing** → `srcset` entries are downloaded and rewritten
- **Resume/PDF not opening** → binary assets are saved directly
- **XML warnings** → install `lxml` or ignore (HTML fallback is handled)

---

## Ethical & Legal Notice

This tool is for **educational and authorized testing only**. Always respect terms of service and robots policies.

---

## Future improvements (optional)

- Headless rendering (Playwright) for JS-heavy sites
- robots.txt enforcement
- JSON crawl reports
- Security header analysis
- Rate limiting and concurrency
- Authentication support for authorized environments

---

## Final note

This project is designed to be **honest, technically correct, and impressive without exaggeration**. It demonstrates strong understanding of web architecture, crawling logic, and security boundaries.

---

## Support MultiHAT Open-Source

If you find this project useful in your workflow, development, or security research, consider supporting **SagarBiswas-MultiHAT** in its ongoing maintenance and future updates (supports both One-Time and Monthly backing):

[![Support on Polar.sh](https://img.shields.io/badge/Support%20on-Polar.sh-0062FF?style=for-the-badge&logo=githubsponsors&logoColor=white)](https://buy.polar.sh/polar_cl_I7DT5UQNUs1Qjb4VrFYOiRA2nkb3RVVUJCnjr3o5SGK)
[![Portfolio](https://img.shields.io/badge/Portfolio-sagarbiswas.multihat.dev-2563EB?style=for-the-badge&logo=todoist&logoColor=white)](https://sagarbiswas.multihat.dev/)
[![Agency](https://img.shields.io/badge/Agency-multihat.dev-111827?style=for-the-badge&logo=vercel&logoColor=white)](https://multihat.dev)
