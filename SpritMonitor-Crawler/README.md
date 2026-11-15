# Spritmonitor.de Crawler

This crawler crawls **spritmonitor.de** in the vehicle fueling records;
extracting useful data (e.g. trip_distance, driving_style, etc.) and
appends each record to a CSV file. See examples: `volkswagen_e_golf.csv` /
`mitsubishi_imiev.csv`

## Requirements

```
pip install -r requirements.txt
```

Selenium/Chrome is only needed when the crawler drives a real browser. The
parsing and CSV logic can be used (and tested) without it.

## Usage as a module

`crawl_vehicle()` takes the CSV path, the vehicle URL and the factory energy
consumption as arguments, so the same process can crawl several vehicles into
one file:

```python
from vehicle_crawler import crawl_vehicle

result = crawl_vehicle(
    csv_path='./mitsubishi_imiev.csv',
    url='https://www.spritmonitor.de/en/detail/786327.html',
    manufacturer_ecr=12.7,
)

print(result.termination_reason, result.stopped_page, result.written_rows)
for page in result.pages:
    print(page.page_number, page.written_rows, page.skipped_rows,
          page.termination_reason, page.error_summary)
```

`crawl_vehicle()` returns a `CrawlResult` whose `pages` list holds one
`PageResult` per processed page:

| field | meaning |
| --- | --- |
| `page_number` | page that was processed (starting at 1) |
| `written_rows` | rows appended to the CSV from this page |
| `skipped_rows` | non-fueling rows skipped on this page |
| `termination_reason` | `正常末页`, `表格缺失`, `其他异常` (plus `达到页数上限` when `max_pages` is used), or `None` for pages that were followed by another page |
| `error_summary` | short exception summary when the crawl stopped abnormally |

`CrawlResult` also exposes the totals `written_rows` / `skipped_rows`, the
`stopped_page`, the overall `termination_reason`, and `to_dict()` for JSON
serialisation.

Behaviour:

* Pages are requested through the `page` query parameter, starting at 1.
* The header row is written only when the CSV file is empty, so repeated calls
  never duplicate it.
* A row without a `fuel_date` is skipped as a non-fueling row.
* Unparsable numeric fields (`odometer`, trip distance, quantity, consumption)
  are written as empty cells instead of aborting the page.
* `ecr_deviation` is empty when consumption or the manufacturer value is missing.
* A page without an `itemtable` table terminates the crawl with `表格缺失`; later
  pages are not visited. Any other exception terminates with `其他异常`. Rows
  already written are flushed and kept on disk.

To use an externally owned browser (e.g. one already logged in), pass it as
`driver=`; it will not be closed by the crawler.

## Command line

```bash
python vehicle_crawler.py ./mitsubishi_imiev.csv \
    https://www.spritmonitor.de/en/detail/786327.html 12.7
```

The CLI prints the per-page result as JSON and exits with status 0 only when the
last page was reached normally.

## Tests

The tests run fully offline with local HTML fixtures and a fake page reader:

```bash
python -m pytest tests
```
