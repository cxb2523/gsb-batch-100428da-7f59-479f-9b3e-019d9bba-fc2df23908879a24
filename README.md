# Google Maps Scraper Examples (Python & Node.js)

![Python 3.10 or newer badge](https://img.shields.io/badge/python-3.10+-blue) ![Node.js 18 or newer badge](https://img.shields.io/badge/node.js-18+-green)

[![HasData, the Google Maps API the API examples call](banner.png)](https://hasdata.com/?utm_source=github&utm_medium=syndication&utm_campaign=how-to-scrape-google-maps&utm_content=google-maps-scraper-readme)

This repository contains working examples of scraping **Google Maps search results** using:

* **Selenium**
* **Playwright (with stealth)**
* **[HasData Google Maps API](https://hasdata.com/apis/google-maps-search-api?utm_source=github&utm_medium=syndication&utm_campaign=how-to-scrape-google-maps&utm_content=google-maps-scraper-readme)**

in both **Python** and **Node.js**. Each method includes clean and minimal code samples with working selectors and data saving logic.


## Table of Contents

1. [Requirements](#requirements)
2. [Project Structure](#project-structure)
3. [Scraper Examples](#scraper-examples)
   * [Selenium](#selenium)
   * [Playwright + Stealth](#playwright--stealth)
   * [HasData API](#hasdata-api)


## Requirements

**Python 3.10+** or **Node.js 18+**

### Python Setup

Install required packages:

```bash
pip install selenium pandas playwright playwright-stealth
playwright install
```

The second line downloads the browsers Playwright drives. `pandas` and
`playwright-stealth` are optional: the listing scrapers write CSV with the
standard-library `csv` module and run without stealth when the package is
missing.

### Node.js Setup

Install required packages (a `package.json` is provided):

```bash
cd NodeJS
npm install
```

The API examples need only `axios` from that list.

## Project Structure

The two folders mirror each other, one script per method.

```
google-maps-scraper/
│
├── Python/
│   ├── maps_common.py            # shared scroll/parse/CSV logic
│   ├── selenium_scraper.py
│   ├── playwright_scraper.py
│   ├── hasdata_api_scraper.py
│   ├── hasdata_reviews_scraper.py
│
├── NodeJS/
│   ├── maps_common.js            # shared scroll/parse/CSV logic
│   ├── package.json
│   ├── selenium_scraper.js
│   ├── playwright_scraper.js
│   ├── hasdata_api_scraper.js
│   ├── hasdata_reviews_scraper.js
│
├── tests/                        # fixture-page acceptance tests (no live Maps)
│   ├── fixtures/
│   ├── test_python_scrapers.py
│   ├── run_tests.js
│   └── README.md
│
└── README.md
```

Each listing scraper captures the same seven fields — business name, rating,
review count, category, services, image, and detail URL — and always writes
the fixed header `Name,Rating,Reviews,Category,Services,Image,Detail URL`.

## Reliable termination and honest output

A fixed number of scrolls is not trustworthy: the panel can reach its bottom
early, a card can miss a rating or an image, and a search can return nothing
at all. Both browser stacks (Python and Node.js, Selenium and Playwright)
therefore share identical behavior, implemented in `maps_common`:

* **Scroll stall detection** — after every scroll round the panel's
  `scrollHeight` is compared with the previous round. Two consecutive rounds
  without any growth stop the loop early (`reached_bottom`); otherwise it
  ends at the configured cap (`max_scrolls_reached`). `max_scrolls` and
  `scroll_pause` keep their original meaning.
* **Per-field fault tolerance** — each field is extracted independently and a
  missing element becomes an empty string, so one incomplete card can never
  fail the record or the run. Rating is still the number from the
  `aria-label`; the review count is parsed from the text and its thousands
  separators are removed.
* **Zero results** — a run with no cards still writes a header-only CSV and
  prints an explicit "0 records collected" message instead of a fake success
  line. Columns are never dropped, even if every card lacks a field.
* **Truthful console summary** — every run prints the records actually
  written and why scrolling stopped (reached bottom or scroll limit).

The scrapers accept a local page URL and headless flag (via the
`MAPS_URL`, `MAPS_OUTPUT`, `MAPS_MAX_SCROLLS`, `MAPS_SCROLL_PAUSE`,
`MAPS_HEADLESS` and `MAPS_BROWSER_CHANNEL` environment variables), which the
test suite uses to run fully offline against an injected fixture page. See
[`tests/README.md`](tests/README.md) for the four acceptance scenarios.

## Scraper Examples

Three routes to the same listings, pick by how defended your volume is.

### Selenium

Classic Google Maps scraping using Selenium with visible browser.

| Parameter      | Description              | Example                |
| -------------- | ------------------------ | ---------------------- |
| `query`        | Search query             | `"pizza in New York"`  |
| `max_scrolls`  | Number of scroll cycles  | `10`                   |
| `scroll_pause` | Pause between scrolls    | `2` seconds            |
| `max_scrolls`  | Scroll repetitions       | `10`                   |
| `output_file`  | Output CSV/JSON filename | `"maps_data.csv/json"` |

Selenium is the baseline, it breaks first when Maps changes markup.

### Playwright + Stealth

Runs headless or headful with stealth mode to avoid detection.

| Parameter     | Description              | Example                    |
| ------------- | ------------------------ | -------------------------- |
| `query`       | Search query             | `"restaurants in Chicago"` |
| `headless`    | Headless mode or not     | `True`                     |
| `scroll_pause`| Pause between scrolls    | `2` seconds                |
| `max_scrolls` | Scroll repetitions       | `10`                       |
| `output_file` | Output CSV/JSON filename | `"output.csv"`             |

Stealth keeps the scroll loop alive noticeably longer than the default profile.

### HasData API

Use the Google Maps scraping API by HasData, no browser automation needed.

| Parameter      | Description                   | Example                        |
| -------------- | ----------------------------- | ------------------------------ |
| `api_key`      | Your HasData API key          | `"your-key"`                   |
| `query`        | Search query                  | `"bars near San Francisco"`    |
| `output_file`  | Output file                   | `"results.json / results.csv"` |

One request returns parsed listings, so there are no selectors to maintain.

### HasData Reviews Feed

`hasdata_reviews_scraper` collects the full review feed of one place. Search resolves the place's `dataId`, then the reviews endpoint returns author, rating, date and text per review, saved to CSV. Verified live, a coffee-shop query returned its place and 8 reviews with full texts.

| Parameter      | Description                   | Example                        |
| -------------- | ----------------------------- | ------------------------------ |
| `api_key`      | Your HasData API key          | `"your-key"`                   |
| `query`        | Search query for the place    | `"coffee shop, Austin, TX"`    |
| `output_file`  | Output CSV file               | `"reviews.csv"`                |

The listing scrapers above capture the review count, this one captures the reviews themselves.

## Notes

* **Selectors change**, so verify current class names on Google Maps before a long run.
* **Rate limiting** responds to random delays, proxies, or the API route.
* **For heavy usage**, prefer the API or your own headless proxy farm.


## Disclaimer

These examples are for **educational purposes** only. Learn more about [the legality of web scraping](https://hasdata.com/blog/is-web-scraping-legal?utm_source=github&utm_medium=syndication&utm_campaign=how-to-scrape-google-maps&utm_content=google-maps-scraper-readme).



## 📎 More Resources

* [How to Scrape Google Maps Data Using Python](https://hasdata.com/blog/how-to-scrape-google-maps?utm_source=github&utm_medium=syndication&utm_campaign=how-to-scrape-google-maps&utm_content=google-maps-scraper-readme), the tutorial the listing scrapers follow
* [How to Scrape Google Maps Reviews](https://hasdata.com/blog/scrape-google-maps-reviews?utm_source=github&utm_medium=syndication&utm_campaign=how-to-scrape-google-maps&utm_content=google-maps-scraper-readme), the tutorial behind the reviews feed script
* [Join the community on Discord](https://discord.com/invite/QeuPtWpkAt)

* [Star this repo if helpful ⭐](#)
