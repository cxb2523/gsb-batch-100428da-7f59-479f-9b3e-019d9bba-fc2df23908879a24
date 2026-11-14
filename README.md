## magic_google

[![](https://img.shields.io/pypi/v/magic_google.svg)](https://pypi.org/project/magic-google/)

### 1.What's magic_google
This is an easy Google Searching crawler that you can get anything you want in the page by using it.

During the process of  crawling,you need to pay attention to the limitation from google towards ip address and the warning of exception , so I suggest that you should pause running the program and own the Proxy ip

php - [MagicGoogle](https://github.com/howie6879/php-google)

### 2.How to Use?
Run
``` shell
pip install magic_google
# Or
pip install git+https://github.com/howie6879/magic_google.git
# Or
git clone https://github.com/howie6879/magic_google.git
cd magic_google
vim google_search.py
# Or 
python setup.py install
```
Example
``` python
from magic_google import MagicGoogle
import pprint

# Or PROXIES = None
PROXIES = [{
    'http': 'http://192.168.2.207:1080',
    'https': 'http://192.168.2.207:1080'
}]

# Or MagicGoogle()
mg = MagicGoogle(PROXIES)

#  Crawling the whole page
result = mg.search_page(query='python')

# Crawling url
for url in mg.search_url(query='python'):
    pprint.pprint(url)
    
# Output
# 'https://www.python.org/'
# 'https://www.python.org/downloads/'
# 'https://www.python.org/about/gettingstarted/'
# 'https://docs.python.org/2/tutorial/'
# 'https://docs.python.org/'
# 'https://en.wikipedia.org/wiki/Python_(programming_language)'
# 'https://www.codecademy.com/courses/introduction-to-python-6WeG3/0?curriculum_id=4f89dab3d788890003000096'
# 'https://www.codecademy.com/learn/python'
# 'https://developers.google.com/edu/python/'
# 'https://learnpythonthehardway.org/book/'
# 'https://www.continuum.io/downloads'

# Get {'title','url','text'}
for i in mg.search(query='python', num=1):
    pprint.pprint(i)
    
# Output
# {'text': 'The official home of the Python Programming Language.',
# 'title': 'Welcome to Python .org',
# 'url': 'https://www.python.org/'}
```
You can see [google_search.py](./examples/google_search.py)

### 2.1 Batch collection with `search_batch`

`search_page`, `search_url` and `search` keep their previous behaviour and return shape.
If you need to crawl many keywords at once, use `search_batch`: it walks every keyword
page by page, keeps the pages that already succeeded, and returns a resumable batch
result instead of stopping at the first problem.

``` python
from magic_google import MagicGoogle

mg = MagicGoogle()
batch = mg.search_batch(["python", "golang"], num=10, max_pages=5)

for item in batch["results"]:
    print(item["query"], item["status"], item["pages"], len(item["urls"]), item["reason"])

# Resume later: already completed keywords are not requested again, the others
# continue from the next page after the last completed one.
batch = mg.search_batch(["python", "golang"], num=10, max_pages=5, previous=batch)
```

The status values are also exported as constants, so callers can branch without hard
coding strings: `STATUS_COMPLETED`, `STATUS_BLOCKED`, `STATUS_NETWORK_ERROR`,
`STATUS_PAGE_LIMIT` (importable from `magic_google`).

Arguments:

- `queries`: an iterable of keywords (a single string is also accepted). Duplicates are
  collapsed; blank or non-string keywords raise `ValueError`.
- `language`, `num`, `pause`, `country`: same meaning as in `search_page`.
- `start`: first result offset for new keywords. Defaults to `0`.
- `step`: offset added per page. Defaults to `num` (or `10` when `num` is not given).
- `max_pages`: page cap for each keyword in one call. Defaults to `None` (keep paging
  until an empty page or a failure). `pages` in the result keeps accumulating across
  resumed runs, but every call gets its own page budget, so a resumed run can continue.
- `previous`: a previous `search_batch` result, or its `results` list.

Each keyword stops on its own when a page yields no parsed result, when Google answers
with a verification page, when the request fails, or when `max_pages` is reached.
Other keywords are not affected.

The returned dict has the shape:

``` python
{
    "results": [
        {
            "query": "python",
            "status": "completed",  # completed / blocked / network_error / page_limit
            "reason": "no results on page start=20",
            "pages": 3,             # pages fetched successfully, including the empty one
            "urls": ["https://www.python.org/", ...],
            "stopped_at": 20,       # where this run stopped (see below)
            "next_start": None,     # resume offset for the next run, None when completed
            "finished": True,
        },
        ...
    ],
    "finished": False,  # True only when every keyword is completed
}
```

Status meanings:

- `completed`: an empty result page was reached (normal end).
- `blocked`: Google returned HTTP 403/429, redirected to a verification page
  (`/sorry/`, `ipv4.google.com/sorry`, ...), or served a captcha/interstitial page.
- `network_error`: the request raised, or the response could not be decoded/parsed.
- `page_limit`: `max_pages` was reached while results were still available.

`pages` only counts pages that were actually fetched and parsed, so a keyword blocked on
its first request reports `pages: 0`. `stopped_at` is the offset that ended the run: the
page that failed or came back empty, or the next page when `max_pages` cut the run short.
On resume, keywords whose status is `completed` are returned as-is without any request;
the others retry from `next_start`, so the page that failed is requested again and `urls`
accumulate across runs.

**If  you need a big amount of querie but only having an ip address,I suggest  you can have a time lapse between 5s ~ 30s.**

The reason that it always return empty might be as follows:

```html
<HTML><HEAD><meta http-equiv="content-type" content="text/html;charset=utf-8">
<TITLE>302 Moved</TITLE></HEAD><BODY>
<H1>302 Moved</H1>
The document has moved
<A HREF="https://ipv4.google.com/sorry/index?continue=https://www.google.me/s****">here</A>.
</BODY></HTML>
```



