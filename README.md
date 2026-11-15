# pinscrape

<p style="text-align: center">
    <img src="image/banner.png" alt="Logo" width="80%">
</p>

[![built with Python3](https://img.shields.io/badge/built%20with-Python3.6+-red.svg)](https://www.python.org/)

### This package can be used to scrape images from pinterest just by using any search keywords. Install it just by using <br><br>
`pip install pinscrape`

### How to use?
```python
from pinscrape import scraper, Pinterest


keyword = "messi"
output_folder = "output"
proxies = {}
number_of_workers = 10
images_to_download = 1

def using_search_engine(): # This is deprecated now
    details = scraper.scrape(keyword, output_folder, proxies, number_of_workers, images_to_download, sleep_time=2)
    if details["isDownloaded"]:
        print("\nDownloading completed !!")
        print(f"\nTotal urls found: {len(details['extracted_urls'])}")
        print(f"\nTotal images downloaded (including duplicate images): {len(details['urls_list'])}")
        print(details)
    else:
        print("\nNothing to download !!", details)


def using_pinterest_apis():
    p = Pinterest(proxies=proxies, sleep_time=2) # you can also pass `user_agent` here.
    images_url = p.search(keyword, images_to_download)
    p.download(url_list=images_url, number_of_workers=number_of_workers, output_folder=output_folder)
    board_details = p.get_pin_details(username='canva', board='design-trends')
    # you can now check any board details of a user.
    print(board_details)
    print(board_details.get('resource_response', {}).get('data', {}).get('created_at'))
```

### Paginated search
`Pinterest.search` accepts an optional `max_pages` argument that follows
Pinterest's own pagination cursor. It defaults to `1`, so omitting it keeps the
historical single-page behaviour; pass a larger number to collect across pages,
and paging stops automatically once Pinterest no longer returns a cursor.

```python
p = Pinterest()
images_url = p.search("messi", max_pages=3)  # up to 3 pages
```

The returned list contains `orig.url` strings in first-appearance order with
cross-page duplicates removed. Per-run details are exposed on `p.last_search`:
whether at least one image was found (`found_image`), how many pages were
requested (`pages_requested`), each page's number and outcome category
(`page_results`: `success`, `http_error`, `parse_error`, `empty_data` or
`no_orig_image`), and the cumulative image count of successful pages
(`images_count`). A failed page is reported there but never discards images
collected from earlier pages.
