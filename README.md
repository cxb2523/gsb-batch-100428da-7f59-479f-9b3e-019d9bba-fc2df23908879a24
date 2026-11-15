# OLX_Scraper
An OLX Scraper using Scrapy + MongoDB. It Scrapes recent ads posted regarding requested product and dumps to NOSQL MONGODB.

## About

A Scrapy Program that scrapes recent ads about products and stores them in MONGODB Database.
All the information regarding product to be searched is in <strong> args.py </strong>
<p>Change values after return command</p>

## Behaviour (v1.1)

The crawl is fault tolerant. One bad advertisement never aborts the run:

* **Unparseable price** (`Negotiable`, `Call for price`, ...): that ad is
  skipped and counted, the remaining ads are processed normally.
* **Minimum price**: ads below `minimumprice` from `args.py` are filtered out
  (same semantics as before, `minimumprice` empty or `0` disables the filter).
* **Missing phone / description / seller / date on a detail page**: an explicit
  fallback value (`"Not available"`) is stored and the item carries
  `phone_missing` / `description_missing` / `seller_missing` / `date_missing`
  flags so a consumer can tell a real value from a degradation.
* **Broken detail request** (timeout, 404, connection error): that ad is
  skipped and counted.
* **List page without ads**: the crawl finishes successfully with zero items
  and `list_page_empty = True` (a legitimate "no results").
* **Unrecognisable list page** (layout changed, blocked page, captcha): the
  crawl is reported as a **failure** (`failure_reason = "list_page_invalid"`).
  It does not silently produce an empty successful result, and
  `scrapy crawl` exits with status `1`.

### Per-crawl outcome

Every crawl ends with the same summary in Scrapy stats and in the log line
`OLX crawl finished[: with failure]: {...}`:

| stat | meaning |
| --- | --- |
| `olx/crawl/status` | `success` / `failed` |
| `olx/crawl/failure_reason` | set when `failed` |
| `olx/crawl/finish_reason` | Scrapy finish reason |
| `olx/list_page/empty` | list page valid and contained no ads |
| `olx/ads/seen` | ad entries on the list page |
| `olx/ads/yielded` | ads successfully turned into items |
| `olx/ads/skipped` | ads skipped (sum of `skip_reasons`) |
| `olx/ads/skip/<reason>` | skipped per reason (`price_unparseable`, `detail_request_failed`, ...) |
| `olx/ads/filtered_below_min_price` | ads dropped by the `minimumprice` filter |
| `olx/detail/missing_phone`, `olx/detail/missing_description` | degraded items |
| `olx/items/written` / `inserted` / `updated` / `write_failed` | MongoDB pipeline results |
| `olx/pipeline/disabled` | items were produced but no pipeline consumed them |

## Usage

```bash
pip install -r olx_scraper/requirements.txt
cd olx_scraper
scrapy crawl scrape_olx
```

`city`, `product` and `minimumprice` are still configured in
`olx_scraper/args.py`.

### MongoDB

`ITEM_PIPELINES` and the `MONGODB_*` settings ship commented out, so a crawl
works without a running MongoDB (items then simply are not persisted; the
outcome summary marks `pipeline_disabled`).  To persist documents, set a
database and collection name in `settings.py` (or via environment variables of
the same name):

```
MONGODB_SERVER = "localhost"
MONGODB_PORT = 27017
MONGODB_DB = "olx"
MONGODB_COLLECTION = "ads"
```

`settings.py` enables the pipeline automatically as soon as `MONGODB_DB` and
`MONGODB_COLLECTION` are both non-empty; `MONGODB_URI` may be used instead of
server/port.

Preconditions for a real MongoDB: a reachable server (or replica set) and
network access from the machine running Scrapy.  In that mode the
`url` of the ad (its detail link) is the deduplication key: an ad seen again is
**updated** (upsert), never inserted twice.  A failure writing a single document
is logged and counted, and the remaining items are still written.

### Offline verification

No real MongoDB or live OLX access is required to check the behaviour:

```bash
python -m pytest tests -q
```

The suite serves local HTML samples through an in-process download handler and
writes through an injectable in-memory collection
(`MongoDBPipeline(collection=...)`).

## Result
Open MongoDB GUI and check database, Your result should be like Screenshot shown above.
## Gotchas
1-You must have python 3.6 pre-installed to use this software.<br>
2-Make sure mongodb is running before you run spider. 

## If 
If any issue comes do write in issues column. Thanks!
