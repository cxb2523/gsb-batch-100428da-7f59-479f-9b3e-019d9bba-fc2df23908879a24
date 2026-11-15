# Abans spider tests (offline, no network access)

Both test files are driven entirely by locally constructed HTML samples
and never contact buyabans.com.

## Unit tests — parsing helpers and spider callbacks

```bash
python -I tests/test_abans_parsing.py
```

Covers:

- `extract_text` / `extract_price`: missing elements, blank text,
  multi-line and multi-node price markup (the old
  `.getall()[1].split('\n')[1]` crash shapes) — missing data becomes
  `None` instead of raising, and raw label-only blobs are never stored
  as a price.
- `parse_max_page`: `None` / blank / non-numeric text returns `None`
  instead of the old `int(None)` crash.
- `should_continue_pagination`: normal parsed cap keeps the original
  rule; missing cap falls back to the bounded crawl; an empty detail
  list always stops.
- Both spider classes end to end at callback level: partial items keep
  every other field plus `detailed_url`; listing pages without the
  pagination element still advance.

## Engine-level crawl — real Scrapy engine, local fixtures

```bash
python -I tests/e2e_offline_crawl.py
```

Runs both `abans_mobile` and `abans_laptop` through `CrawlerProcess`
with a fixture downloader middleware that only serves pre-registered
`file://` URLs (any other URL is refused), so nothing can leave the
machine. It asserts four items are produced: two complete ones with
multi-line prices parsed correctly and two partial ones whose missing
fields are serialized as `null`.
