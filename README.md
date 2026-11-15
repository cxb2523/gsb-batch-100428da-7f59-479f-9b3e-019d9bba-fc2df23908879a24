# TikTok Deep Bio Scraper

**English** · [العربية](README.ar.md)

A pure-API (no browser, no Selenium) tool that crawls TikTok deeply to discover
accounts and extract **phone numbers from their bios**, then guesses each
number's **country** with a probability distribution.

It works by manually signing TikTok web-API requests and using
[`curl_cffi`](https://github.com/lexiforest/curl_cffi) with browser
impersonation to bypass Akamai's TLS fingerprinting.

> **Disclaimer:** This project is for educational and research purposes only.
> You are responsible for complying with TikTok's Terms of Service and all
> applicable privacy/data-protection laws (e.g. GDPR). Do not use it to harvest
> or misuse personal data.

---

## Features

- **Pure API** — no browser automation; direct signed HTTP requests.
- **Deep, continuous crawling** — BFS over the follower graph: an account →
  its followers → their followers → … up to a configurable depth. The frontier
  grows on its own, so the search keeps going and deepens automatically.
- **Keyword / hashtag seeding** — discover accounts by search keywords.
- **Phone extraction** — supports Arabic/Persian and Latin digits, WhatsApp
  links, and common separators.
- **Country detection with probabilities** — a hybrid algorithm using the
  number structure plus bio signals (flags, city/country names, dial codes).
- **Automatic business-type classification** — each account is categorized
  (real estate, cars, restaurants, delivery, marketing, medical, …) from its
  bio using a weighted-keyword algorithm, computed automatically as numbers are
  collected.
- **Persistent state** — users already crawled are recorded and skipped on
  future runs (no re-crawling).
- **Incremental, de-duplicated JSON output.**

---

## How it works

Requests are signed client-side the way TikTok's `webmssdk` does:

| Signature     | Source                                   |
| ------------- | ---------------------------------------- |
| `X-Gnarly`    | computed in `gnarly.py` (ChaCha-based)   |
| `X-Bogus`     | `1` (a static placeholder is sufficient) |
| `msToken`     | random 128-char string                   |
| `X-Dynosaur`  | reusable token captured from a browser, stored in `config.json` (search only) |

Endpoints used:

- User search: `/api/search/user/full/` (needs `X-Dynosaur`)
- Follower list: `/api/user/list/?scene=67` (no `X-Dynosaur`)

Plain `requests` gets blocked (empty body) because of TLS/JA3 fingerprinting;
`curl_cffi` with `impersonate` solves this.

---

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the example config and fill in your own values:

```bash
cp config.example.json config.json
```

Edit `config.json`:

- **`cookie`** — a full logged-in cookie string from your browser. It **must**
  contain `sessionid=...` (follower lists require login).
- **`x_dynosaur`** — a token captured from a search request in the browser's
  Network tab. It is reusable across queries; refresh it if search starts
  returning empty responses.
- **`impersonate`** — `curl_cffi` browser profile (default `firefox133`).
- Other tunables: `max_followers_per_user`, `max_depth`, `max_users`,
  `delay_min`/`delay_max`, `keywords`, `output`, `state`.

> `config.json` is git-ignored so you never commit your real cookies/tokens.

### How to capture `cookie` and `x_dynosaur`

1. Log in to `tiktok.com` in your browser.
2. Open DevTools → Network, and perform a **user search**.
3. Open a request to `/api/search/user/full/`:
   - Copy the full `Cookie` request header → `cookie`.
   - Copy the `X-Dynosaur` query parameter → `x_dynosaur`.

---

## Usage

### 1) Single-account follower scan (`search_bio.py`)

```bash
python3 search_bio.py coderoot.ksa
python3 search_bio.py user1 user2 --max-followers 1000
python3 search_bio.py someuser --no-seed
```

### 2) Deep continuous crawler (`deep_search.py`)

```bash
# From keywords / hashtags
python3 deep_search.py --keywords "مطعم" "عقارات" "توصيل" --max-depth 6

# From seed accounts
python3 deep_search.py --seeds coderoot.ksa --max-depth 6 --max-followers 800

# Larger limits
python3 deep_search.py --keywords "متجر" --max-users 200000 --max-depth 6
```

| Option             | Description                                   | Default            |
| ------------------ | --------------------------------------------- | ------------------ |
| `--keywords`       | search keywords/hashtags to start from        | from config        |
| `--seeds`          | seed usernames to start from                  | —                  |
| `--max-depth`      | expansion depth in the follower tree          | 4                  |
| `--max-followers`  | max followers fetched per account             | 1000               |
| `--max-users`      | max accounts to process (safety cap)          | 1000000            |
| `--state`          | state file of already-crawled users           | `output/crawl_state.json` |
| `--outcomes`       | JSONL file with one outcome record per account | `output/crawl_outcomes.jsonl` |
| `--page-retries`   | retries per account for a failed/anomalous page | 3                  |
| `--retry-backoff`  | base seconds for exponential retry backoff    | 1.0                |
| `--reset-state`    | ignore state and start fresh                  | off                |
| `--delay-min/max`  | random delay between requests (seconds)       | 0.3 / 1.0          |

- Runs **continuously** and deepens as long as the frontier is non-empty.
- Stop anytime with **Ctrl+C**; progress and state are saved.
- Private accounts are not expanded (their follower list isn't public).
- **Resilient pagination**: empty responses, non-zero `status_code` and cursor
  anomalies (repeated/regressing cursor) are retried per account with bounded
  exponential backoff. If retries run out, only that account's pagination stops —
  followers already fetched are kept and their bios are still scanned.
- Every processed account leaves an **outcome record** (account, depth, followers
  fetched, complete?, failure reason, attempts) in the outcomes JSONL file, and the
  run ends with a success/partial/failed summary. Only fully-crawled accounts are
  written to the state file; partially-failed accounts are retried on the next run.

### 3) Enrich an existing file (`enrich_country.py`, `enrich_categories.py`)

```bash
python3 enrich_country.py                  # add/refresh country info
python3 enrich_categories.py               # add/refresh business-type category
python3 enrich_country.py path/to/file.json
```

---

## Output format

Results are saved to `output/tiktok-bio.json` (a de-duplicated JSON array):

```json
{
  "name": "Restaurant Mawazin Dubai",
  "username": "bouchtadubai",
  "phone": "0547166686",
  "country": "United Arab Emirates",
  "country_ar": "الإمارات",
  "country_iso": "AE",
  "dial_code": "+971",
  "country_confidence": 0.636,
  "country_probs": { "AE": 0.636, "SA": 0.364 },
  "category": "Restaurants & Food",
  "category_ar": "مطاعم وأطعمة",
  "category_confidence": 1.0,
  "category_tags": ["restaurant"],
  "bio": "... Deira Dubai ...",
  "source": "search:مطعم"
}
```

`source` is one of `search:<keyword>`, `seed:@<user>`, or `follower_of:@<user>`.

---

## Country detection algorithm

1. **Explicit international code** (`+` / `00` / known code at the start) → high confidence.
2. **Local format** — national mobile prefixes:
   - `01[0125]` (11 digits) → Egypt.
   - `05x` (10 digits) → Saudi Arabia / UAE, split by prefix
     (`053/057/059` = Saudi, `052` = UAE, `050/054/055/056/058` = shared).
   - `920 / 800` → Saudi unified/toll numbers.
   - 8 digits without a leading 0 → Gulf 8-digit numbering.
3. **Bio signals** — flag emojis (🇸🇦🇦🇪🇪🇬…), city/country names, and dial
   codes written in the text boost the matching candidate.

Scores are normalized into a probability distribution (`country_probs`), keyed
by ISO code, and the top candidate becomes `country`/`country_confidence`.

## Business-type classification

Each account is classified into one of ~17 categories (Real Estate, Cars,
Delivery, Marketing, Restaurants, Clothing, Beauty, Medical, Education,
Cleaning, Furniture, Contracting, Printing, Tech, Finance, Travel, Store) using
weighted keyword lists over the bio and name. Scores are normalized into a
probability distribution; the top category becomes `category`/`category_ar`
with `category_confidence`, and `category_tags` lists the top-3 category keys.
Accounts with no clear business signal are left unclassified (`null`) rather
than force-fitted.

---

## Project structure

```
.
├── search_bio.py         # single-account follower scan (pure API)
├── deep_search.py        # deep continuous BFS crawler
├── country.py            # phone -> country probability algorithm
├── categories.py         # bio -> business-type classification
├── phones.py             # phone-number extraction from bio text
├── enrich_country.py     # add country info to an existing results file
├── enrich_categories.py  # add category info to an existing results file
├── gnarly.py             # X-Gnarly request-signature generator
├── config.example.json   # config template (copy to config.json)
├── requirements.txt
└── output/               # results & state (git-ignored)
```

---

## About Storage TE

This project is built and maintained by **[Storage TE](https://storage-te.com/)** —
a software company specialized in **reverse engineering**, automation, growth
tooling, custom programming, and AI. We build web/mobile apps, admin dashboards,
data-extraction and lead-generation pipelines (e.g. Google Maps & social leads),
signing/anti-bot research, and digital-growth platforms for social media.

Learn more at **[storage-te.com](https://storage-te.com/)**.

## Maintainer, company & contact 👤

| | |
| --------- | ---------------------------------------- |
| Developer | Mostafa Al-Bagouri                       |
| Company   | [Storage TE](https://storage-te.com/)    |
| WhatsApp  | +20 100 199 5914                         |
| Email     | info@storage-te.com                      |

## Support this project ☕

If this toolkit is useful to you, optional support helps maintain and improve it
and related work. Pick whatever works best for you.

| Channel | How to support |
| ------- | -------------- |
| PayPal | [paypal.me/sofaapi](https://paypal.me/sofaapi) |
| Binance Pay / UID | `1138751298` — send from the Binance app (Pay / internal transfer when available). |
| Binance — deposit (web) | Deposit crypto (Binance) — sign in, pick the asset, then select **BSC (BEP20)**. |
| BSC address (copy) | `0x94c5005229784d9b7df4e7a7a0c3b25a08fd57bc` |

> **Network:** Use **BSC (BEP-20) only**. This address is for **USDT (BEP-20)**
> and **BTC on BSC** (Binance-Peg / in-app "BTC" on BSC), matching the Binance
> deposit flow. Do **not** send native Bitcoin (on-chain BTC), ERC-20, or NFTs
> to this address.

### Deposit QR codes (scan in Binance or any BSC wallet)

| USDT · BSC | BTC · BSC |
| :--------: | :-------: |
| ![USDT deposit QR - BSC](assets/usdt-bsc-qr.png) | ![BTC on BSC deposit QR](assets/btc-bsc-qr.png) |

Both QR codes encode the same BSC (BEP-20) address above.

Arabic documentation: **[README.ar.md](README.ar.md)**

---

## Keywords

`tiktok` · `tiktok-api` · `tiktok-scraper` · `web-scraping` · `crawler` ·
`osint` · `lead-generation` · `phone-numbers` · `data-extraction` ·
`reverse-engineering` · `x-gnarly` · `x-bogus` · `msToken` · `curl-cffi` ·
`tls-fingerprinting` · `country-detection` · `business-classification` ·
`followers` · `bio-scraper` · `arabic` · `python` · `automation` · `storage-te`

---

## Disclaimer

For research and education only; respect TikTok's Terms of Service and all
applicable laws.

## License

[MIT](LICENSE)
