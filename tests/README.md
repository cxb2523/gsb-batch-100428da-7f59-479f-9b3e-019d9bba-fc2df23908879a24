# Scraper acceptance tests

No real Google Maps access is required.  The browser tests drive a local
fixture page (`fixtures/fixture.html`) that reproduces the Maps result-panel
markup the scrapers target and lets tests inject card data.

## Fixture scenarios

The scenario is chosen through the URL hash, e.g.
`fixture.html#early-bottom`:

| Scenario       | What it proves                                                            |
| -------------- | ------------------------------------------------------------------------ |
| `normal`       | Multiple fully populated cards parse into all seven CSV columns.         |
| `missing`      | Cards missing ratings, images, names, etc. degrade to empty strings.     |
| `empty`        | A feed with zero cards still writes a header-only CSV and reports 0.     |
| `early-bottom` | A panel that never grows stops scrolling after two stalled rounds.       |
| `growing`      | Lazy-loaded batches append on scroll, then growth stops and it stalls.   |

The same fixture is used by both language stacks, so Python and Node.js
exercise identical page behavior. The first four are the required acceptance
cases; `growing` additionally proves the loop keeps scrolling while content
really is being appended (rather than stopping after a fixed two stalls) and
only ends once lazy loading finishes.

## Running the tests

### Python

Unit tests (no browser, always available):

```bash
python -I tests/test_python_scrapers.py
```

Browser integration tests (Selenium + Playwright against the fixture page):

```bash
# bash
RUN_BROWSER_TESTS=1 python -I tests/test_python_scrapers.py
```

```cmd
:: Windows cmd
set RUN_BROWSER_TESTS=1
python -I tests/test_python_scrapers.py
```

### Node.js

Unit tests:

```bash
node --test tests/run_tests.js
```

Browser integration tests:

```bash
RUN_BROWSER_TESTS=1 node --test tests/run_tests.js
```

If Playwright's bundled Chromium is not installed, the Playwright tests can
drive the system Chrome instead:

```bash
MAPS_BROWSER_CHANNEL=chrome RUN_BROWSER_TESTS=1 node --test tests/run_tests.js
```

## Sandbox / CI notes

* On machines where `LOCALAPPDATA`/`APPDATA` are redirected away from the
  normal profile tree, Chrome refuses remote debugging. The scrapers detect
  this and point those variables back at the standard per-user folders
  (`normalize_browser_env` / `normalizeBrowserEnv`); it is a no-op elsewhere.
* Selenium sessions use a dedicated, per-run profile directory, pipe-based
  DevTools and hardened launch flags. If a sandboxed Chrome still drops its
  renderer, the Selenium scraper transparently retries with a fresh session
  (up to three times) rather than failing the run.
* `CHROMEDRIVER_PATH` overrides driver discovery when Selenium Manager cannot
  reach the network.

## What is asserted across all four scenarios

* **Scrolling**: at most `max_scrolls` rounds; two consecutive rounds without
  panel growth end the loop early with `reached_bottom`, otherwise the reason
  is `max_scrolls_reached`.
* **Parsing**: rating comes from the `aria-label` number; the review count
  drops thousands separators; every missing field is an empty string and no
  card failure aborts the run.
* **CSV**: the header is always
  `Name,Rating,Reviews,Category,Services,Image,Detail URL`, including for a
  zero-result run (header-only file).
* **Console output**: the actual number of written records plus the scroll
  stop reason; a zero-result run explicitly says 0 records were collected and
  never prints a "saved N records" success line.
