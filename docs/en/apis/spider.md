# Spider

`Spider` is the entrypoint of the crawler program.
It combines `Item`, `Middleware`, `Request` and other models, to build a strong crawler for you.
You should focus on the following two functions:

- [Spider.start](): the entrypoint
- [parse](): The first parse function, required for subclass of `Spider`

## Core arguments

`Spider.start` arguments:

- after_start: a hook after starting the crawler
- before_stop: a hook before starting the crawler
- middleware: `Middleware` class, can be an object of `Middleware()`, or a list of `Middleware()`
- loop: event loop

## Usage

```python
import aiofiles

from ruia import AttrField, TextField, Item, Spider


class HackerNewsItem(Item):
    target_item = TextField(css_select='tr.athing')
    title = TextField(css_select='a.storylink')
    url = AttrField(css_select='a.storylink', attr='href')

    async def clean_title(self, value):
        return value


class HackerNewsSpider(Spider):
    start_urls = ['https://news.ycombinator.com/news?p=1', 'https://news.ycombinator.com/news?p=2']

    async def parse(self, response):
        async for item in HackerNewsItem.get_items(html=await response.text()):
            yield item

    async def process_item(self, item: HackerNewsItem):
        """Ruia build-in method"""
        async with aiofiles.open('./hacker_news.txt', 'a') as f:
            await f.write(str(item.title) + '\n')


if __name__ == '__main__':
    HackerNewsSpider.start()
```

### Controlling the max number of concurrency

Define an attribute `concurrency` at the subclass of `Spider`. Here is an example:

```python
import ruia


class MySpider(ruia.Spider):
    
    start_urls = ['https://news.ycombinator.com']
    concurrency = 3
    
    async def parse(self, res):
        pass

```

## How It Works?

`Spider` will read links in `start_urls`, and maintains a asynchronous queue.
The queue is a producer consumer model, and the loop will run until no more request functions.

## Per-request outcome records

By default a spider only keeps the cumulative `success_counts` / `failed_counts`
statistics. If you need to know what happened to one specific page (succeeded,
succeeded after retries, or abandoned after exhausting retries), enable the
per-request records:

```python
import ruia


class MySpider(ruia.Spider):
    start_urls = ['https://news.ycombinator.com']
    record_requests = True

    async def parse(self, res):
        pass


spider = MySpider.start()
record = spider.get_request_record('https://news.ycombinator.com')
print(record.outcome, record.attempts, record.status)
```

Every `Request` leaving the queue produces one record with:

- `url`: request URL
- `method`: HTTP method
- `outcome`: `success`, `retried_success` (failed at least once, then
  succeeded) or `failed` (retries exhausted, abandoned)
- `attempts`: how many HTTP requests were actually sent
- `status`: final status code (`-1` when the request was abandoned)
- `error`: error message when the request was abandoned
- `callback`: name of the callback which produced the request
- `parent_url`: URL of the parent request for requests yielded by a callback,
  so a broken pagination chain can be traced

Querying the records:

```python
spider.get_request_record(url)                    # last record of one URL
spider.get_request_records(url=url)               # all records of one URL
spider.get_request_records(outcome='failed')      # records by outcome
spider.get_outcome_counts()                       # {'success': 1, 'retried_success': 1, 'failed': 1}
```

`success` + `retried_success` equals `success_counts`, while `failed` equals
`failed_counts`.

The feature is disabled by default: without `record_requests = True` the
crawling behaviour and the statistics stay exactly the same as before.

