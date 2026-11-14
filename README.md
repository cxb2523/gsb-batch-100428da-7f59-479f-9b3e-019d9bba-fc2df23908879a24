![Python](https://img.shields.io/badge/python-3.10+-blue)
![Node.js](https://img.shields.io/badge/node.js-18+-green)


# Cloudflare 1020 Bypass Examples (Python & Node.js)
[![HasData_bannner](banner.png)](https://hasdata.com/?utm_source=github&utm_medium=syndication&utm_campaign=cloudflare-1020&utm_content=cloudflare-bypass-readme)

This repo contains working examples of bypassing **Cloudflare 1020** errors using **Playwright + Stealth** with **residential proxies**, **rotating headers**, and **basic anti-bot evasion tricks**. Works in **Node.js** and **Python**.

## Table of Contents

1. [Requirements](#requirements)
2. [Project Structure](#project-structure)
3. [Bypass Examples](#bypass-examples)
   * [Playwright Basic](#playwright-basic)
   * [Playwright + Stealth](#playwright--stealth)
   * [Residential Proxy Rotation](#residential-proxy-rotation)
   * [User-Agent + Header Spoofing](#user-agent--header-spoofing)
   * [Human-like Behavior](#human-like-behavior)
   * [Full Flow Example](#full-flow-example)

## Requirements

**Python 3.10+** or **Node.js 18+**

### Python Setup

Required packages:

- `requests`
- `playwright`
- `playwright-stealth`

Install:

```bash
pip install playwright
playwright install
pip install playwright-stealth
```

### Node.js Setup

Required packages:

- `playwright`
- `playwright-extra`
- `playwright-extra-plugin-stealth`

Install:

```bash
npm install playwright playwright-extra playwright-extra-plugin-stealth
```

## Project Structure

```
cf1020-bypass-examples/
│
├── python/
│   ├── basic_playwright.py
│   ├── stealth_playwright.py
│   ├── proxy_rotation.py
│   ├── header_rotation.py
│   ├── human_behavior.py
│   ├── full_flow_example.py
│
├── nodejs/
│   ├── basic_playwright.js
│   ├── stealth_playwright.js
│   ├── proxy_rotation.js
│   ├── header_rotation.js
│   ├── human_behavior.js
│   ├── full_flow_example.js
│
└── README.md
```

Each script shows a different tactic for avoiding Cloudflare 1020. No frameworks, no noise — just straight working examples.

## Bypass Examples


### Playwright Basic

Basic page load using vanilla Playwright (likely to trigger CAPTCHA or 1020).
| Parameter     | Description            | Example                 |
| ------------- | ---------------------- | ----------------------- |
| `url`         | Target URL to load     | `'https://example.com'` |
| `timeout`     | Maximum wait time (ms) | `60000`                 |
| `output_file` | File to save page HTML | `'page.html'`           |


### Playwright + Stealth

Hide `webdriver` flag, spoof plugins, patch headless indicators.

| Parameter    | Description                  | Example                 |
| ------------ | ---------------------------- | ----------------------- |
| `target_url` | URL to visit and scrape      | `'https://example.com'` |
| `headless`   | Run browser in headless mode | `True`                  |
| `timeout`    | Maximum wait time (ms)       | `60000`                 |


### Residential Proxy Rotation

Add a pool of rotating residential proxies for better IP reputation.

| Parameter       | Description                                   | Example                                 |
| --------------- | --------------------------------------------- | --------------------------------------- |
| `target_url`    | URL to visit with rotating headers            | `'https://example.com'`                 |
| `user_agents`   | List of User-Agent strings to rotate          | `['Mozilla/5.0...', 'Chrome/91.0...']`  |
| `extra_headers` | Additional HTTP headers to include (optional) | `{'Accept-Language': 'en-US,en;q=0.9'}` |
| `rotate_every`  | Number of requests before changing header     | `1` (rotate every request)              |


### User-Agent + Header Spoofing

Randomized headers and modern user agents for each session.
| Parameter     | Description                              | Example                                 |
| ------------- | ---------------------------------------- | --------------------------------------- |
| `target_url`  | URL to visit with rotated headers        | `'https://example.com'`                 |
| `user_agents` | List of User-Agent strings to rotate     | `["Mozilla/5.0...", "Safari/537.36"]`   |
| `headers`     | Additional HTTP headers to include       | `{"Accept-Language": "en-US,en;q=0.9"}` |
| `proxy`       | Proxy server address (optional)          | `'http://user:pass@proxy.com:8080'`     |
| `timeout`     | Max time to wait for page load (seconds) | `30`                                    |


### Human-like Behavior

Add mouse movement, scrolling, and timed delays.
| Parameter                     | Description                 | Example                                      |
| ----------------------------- | --------------------------- | -------------------------------------------- |
| `page`                        | Playwright page object      | `page.goto('https://example.com')`           |
| `sleep`                       | Pause execution for realism | `time.sleep(1.5)` / `await sleep(1500)` (ms) |
| `mouse.move(x, y)`            | Move mouse cursor to (x, y) | `page.mouse.move(100, 100)`                  |
| `mouse.click(x, y)`           | Click at coordinates (x, y) | `page.mouse.click(200, 300)`                 |
| `keyboard.press(key)`         | Simulate keyboard key press | `page.keyboard.press('PageDown')`            |
| `mouse.wheel(deltaX, deltaY)` | Scroll page by delta        | `page.mouse.wheel(0, 400)`                   |


### Full Flow Example

All combined: stealth, a random user agent and **proxy failover** — in one
script. Both implementations expose the same entry point and the same result
fields:

| Language | File                          | Function                                         |
| -------- | ----------------------------- | ------------------------------------------------ |
| Python   | `Python/full_flow_example.py` | `scrape_with_proxy_rotation(...)` (async)        |
| Node.js  | `NodeJS/full_flow_example.js` | `scrapeWithProxyRotation(...)` (also exported as `scrape_with_proxy_rotation`) |

#### Flow

1. The proxy pool is shuffled, so every proxy is attempted **at most once, in
   random order** (duplicate entries are removed).
2. For each proxy a **fresh browser context** is created with that proxy and a
   randomly chosen UA from the UA pool, then the target URL is navigated.
3. A failed navigation — proxy unreachable, DNS/TLS/network error, timeout,
   HTTP status ≥ 400, or empty HTML — is recorded and that context is closed.
   The next proxy is tried.
4. The first success returns immediately with the HTML. When every proxy has
   failed, `ProxyRotationError` is raised (Python) / rejected (Node.js); empty
   content is never returned silently.

#### Parameters

The proxy/UA pools stay in-script constants, but each call accepts overrides.
Precedence is **argument > environment variable > constant**.

| Parameter      | Python                                | Node.js                            | Env variable            | Default                     |
| -------------- | ------------------------------------- | ---------------------------------- | ----------------------- | --------------------------- |
| Proxy pool     | `proxies` (list or separated string)  | `proxies` (array or separated string) | `FULL_FLOW_PROXIES`   | `PROXIES` constant          |
| UA pool        | `user_agents`                         | `userAgents`                       | `FULL_FLOW_USER_AGENTS` | `USER_AGENTS` constant      |
| Target URL     | `target_url`                          | `targetUrl`                        | `FULL_FLOW_TARGET_URL`  | `https://example.com`       |
| Headless       | `headless`                            | `headless`                         | `FULL_FLOW_HEADLESS`    | `true`                      |
| Timeout (ms)   | `timeout_ms`                          | `timeoutMs`                        | `FULL_FLOW_TIMEOUT_MS`  | `60000`                     |
| Proxy bypass   | `proxy_bypass`                        | `proxyBypass`                      | `FULL_FLOW_PROXY_BYPASS`| unset                       |
| Print JSON     | — (CLI only)                          | — (CLI only)                       | `FULL_FLOW_PRINT_JSON`  | unset (prints first 500 chars) |

Pool env values accept a JSON array or a newline-separated list; proxies also
accept commas (UA strings contain commas, so use newlines or a JSON array
there). A bare string argument is parsed the same way.

#### Return value (identical field names in both languages)

| Field              | Meaning                                                              |
| ------------------ | -------------------------------------------------------------------- |
| `success`          | Always `true` on a successful return.                                |
| `url`              | Target URL that was loaded.                                          |
| `html`             | Full page HTML.                                                      |
| `proxy`            | The proxy actually used, masked (password replaced by `***`).        |
| `proxy_index`      | Position of that proxy in the pool as supplied — lets you match the masked label back to the exact entry. Also present on failure records. |
| `user_agent`       | The UA actually used.                                                |
| `failed_attempts`  | Ordered list of failures before the success (empty on a first-try win). |
| `attempt_count`    | Total attempts made, i.e. failures + 1 on success.                   |

Each item of `failed_attempts` has:

| Field         | Meaning                                                          |
| ------------- | ---------------------------------------------------------------- |
| `proxy`       | Masked proxy identifier (`http://user:***@host:port`).            |
| `proxy_index` | Index of that proxy in the supplied pool.                        |
| `user_agent`  | UA used for that attempt.                                        |
| `error_type`  | Exception/error name (e.g. `Error`, `TimeoutError`, `HttpStatusError`). |
| `error`       | Error message, truncated to 500 characters.                      |

#### Error when every proxy fails

`ProxyRotationError` carries `target_url`, `failed_attempts` (same records as
above) and `attempted_count`, and its message names the number of proxies
tried, e.g.:

```
All 2 proxy attempt(s) failed for https://example.com.
Attempts: [1] http://127.0.0.1:1 -> Error: net::ERR_PROXY_CONNECTION_FAILED ...;
          [2] http://user:***@127.0.0.1:8080 -> HttpStatusError: HTTP 502 ...
```

An empty/misconfigured pool raises `ValueError` (Python) / a plain `Error`
(Node.js) instead of silently doing nothing.

#### Usage

Python:

```python
import asyncio
from full_flow_example import scrape_with_proxy_rotation, ProxyRotationError

async def run():
    try:
        result = await scrape_with_proxy_rotation(
            proxies=["http://user:pass@proxy-a.example:8000",
                     "http://user:pass@proxy-b.example:8000"],
            user_agents=["Mozilla/5.0 (Windows NT 10.0; Win64; x64) ..."],
            target_url="https://example.com",
            timeout_ms=60000,
        )
    except ProxyRotationError as error:
        print(error.attempted_count, error.failed_attempts)
        return
    print(result["proxy"], result["attempt_count"], len(result["failed_attempts"]))
    print(result["html"][:500])

asyncio.run(run())
```

Node.js:

```js
const { scrapeWithProxyRotation, ProxyRotationError } = require('./full_flow_example');

(async () => {
  try {
    const result = await scrapeWithProxyRotation({
      proxies: ['http://user:pass@proxy-a.example:8000', 'http://user:pass@proxy-b.example:8000'],
      userAgents: ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...'],
      targetUrl: 'https://example.com',
      timeoutMs: 60000,
    });
    console.log(result.proxy, result.attempt_count, result.failed_attempts.length);
    console.log(result.html.slice(0, 500));
  } catch (error) {
    if (error instanceof ProxyRotationError) {
      console.error(error.attempted_count, error.failed_attempts);
    } else {
      throw error;
    }
  }
})();
```

Both files also run standalone. The process exits `1` when every proxy failed
and `2` on a configuration error (empty pool, bad timeout, unparsable proxy):

```bash
FULL_FLOW_PROXIES="http://127.0.0.1:1
http://127.0.0.1:2" FULL_FLOW_USER_AGENTS="Mozilla/5.0 ..." \
FULL_FLOW_TARGET_URL="https://example.com" python Python/full_flow_example.py

FULL_FLOW_PROXIES="http://127.0.0.1:1,http://127.0.0.1:2" \
FULL_FLOW_USER_AGENTS="Mozilla/5.0 ..." \
FULL_FLOW_TARGET_URL="https://example.com" node NodeJS/full_flow_example.js
```

#### Injecting a pool and simulating failures locally

The proxy constants in the files are **unreachable placeholders**, so out of
the box the example can only demonstrate the all-failed path. Inject a real
pool with `FULL_FLOW_PROXIES` or the `proxies` argument (Node.js:
`FULL_FLOW_PROXIES` or `options.proxies`).

To exercise rotation without any real proxy or external network, start local
ports that fail in different ways and point the pool at them:

- a **closed port** → connection refused (acts as a dead proxy);
- a local **HTTP server that replies `502`** → proxy reachable but broken.

Then point `FULL_FLOW_TARGET_URL` at a local page and add
`FULL_FLOW_PROXY_BYPASS="<-loopback>"`, because Chromium bypasses loopback
addresses by default. A minimal Node.js mock (Python's `http.server` works the
same way):

```js
// mock-proxy.js: run `node mock-proxy.js 18081 18082`
const http = require('http');
const refused = Number(process.argv[2]); // keep this port closed
const broken = Number(process.argv[3]);

http.createServer((req, res) => {
  res.writeHead(502, { 'Content-Length': '0' });
  res.end();
}).on('connect', (req, socket) => {
  socket.end('HTTP/1.1 502 Bad Gateway\r\n\r\n');
}).listen(broken, '127.0.0.1', () => {
  console.log(`bad proxy  http://127.0.0.1:${broken}`);
  console.log(`dead proxy http://127.0.0.1:${refused} (nothing listens here)`);
});
```

```bash
# terminal 1: a page to load (and a broken proxy)
python -m http.server 18080 --bind 127.0.0.1
node mock-proxy.js 18081 18082

# terminal 2: every proxy fails -> ProxyRotationError with 2 attempts
FULL_FLOW_PROXIES="http://127.0.0.1:18081,http://127.0.0.1:18082" \
FULL_FLOW_USER_AGENTS="Mozilla/5.0 ..." \
FULL_FLOW_TARGET_URL="http://127.0.0.1:18080/" \
FULL_FLOW_PROXY_BYPASS="<-loopback>" \
python Python/full_flow_example.py
```

A truly working local forward proxy (needed to demonstrate the success path
end-to-end) can be any HTTP proxy that accepts absolute-URI requests; set
`FULL_FLOW_PROXIES` to `dead-port,working-proxy` and the run should record one
failure and then succeed. **Mock results only prove the rotation/error-handling
logic — they are not evidence that the production proxy + target combination
works. Validate the real chain with a real proxy and the real target.**

#### Notes

- Playwright credentials are split out of the proxy URL into
  `username`/`password` automatically; IPv6 hosts are bracketed.
- The Node.js script uses `playwright-extra` + `playwright-extra-plugin-stealth`
  when that plugin actually loads, and falls back to plain `playwright`
  otherwise (some published versions of the plugin are only a placeholder that
  throws). The Python script supports both `playwright-stealth` 1.x and 2.x.
- Only `full_flow_example.py` / `full_flow_example.js` changed; the other demo
  scripts keep their original behavior.



## Disclaimer

These examples are for **educational purposes** only. Learn more about [the legality of web scraping](https://hasdata.com/blog/is-web-scraping-legal?utm_source=github&utm_medium=syndication&utm_campaign=cloudflare-1020&utm_content=cloudflare-bypass-readme).



## 📎 More Resources

* [How to Bypass Cloudflare 1020](https://hasdata.com/blog/bypass-cloudflare-1020?utm_source=github&utm_medium=syndication&utm_campaign=cloudflare-1020&utm_content=cloudflare-bypass-readme)
* [Join the community on Discord](https://discord.com/invite/kckPcEdGWs)
* [Star this repo if helpful ⭐](#)
