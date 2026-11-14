"""
Offline tests for the traversal pagination limit and for the traversal link
domain filtering. No network access is required: responses and spider configs
are built in memory.
"""
from types import SimpleNamespace

import pytest
import scrapy
from scrapy.http import HtmlResponse, Request

from crawlerflow.contrib.spiders.base import WebCrawlerBase
from crawlerflow.core.traversals.generic import GenericLinkExtractor


TRAVERSAL_ID = "pagination_traversal"
TRAVERSAL_STATS_KEY = "crawlerflow-stats/traversals/{}/requests_count".format(TRAVERSAL_ID)


class FakeStatsCollector(object):
    """Minimal stand-in for Scrapy's stats collector."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_value(self, key, default=None, spider=None):
        return self.values.get(key, default)

    def inc_value(self, key, count=1, start=0, spider=None):
        self.values[key] = self.values.get(key, start) + count

    def set_value(self, key, value, spider=None):
        self.values[key] = value


class TraversalTestSpider(WebCrawlerBase):
    name = "traversal-test-spider"

    def __init__(self, stats_values=None, **kwargs):
        super().__init__(**kwargs)
        self.crawler = SimpleNamespace(stats=FakeStatsCollector(stats_values))


def make_manifest(allowed_domains=None):
    return {
        "spiders": [
            {
                "spider_id": "target_spider",
                "spider_settings": {
                    "allowed_domains": allowed_domains if allowed_domains is not None else []
                },
                "traversals": [],
            }
        ]
    }


def make_spider_config(max_pages=3, selector_value=".next"):
    return {
        "spider_id": "source_spider",
        "traversals": [
            {
                "traversal_id": TRAVERSAL_ID,
                "selector_type": "css",
                "selector_value": selector_value,
                "next_spider_id": "target_spider",
                "max_pages": max_pages,
            }
        ],
    }


def make_response(body, url="https://example.com/", meta=None):
    request_meta = {
        "manifest": make_manifest(),
    }
    request_meta.update(meta or {})
    request = Request(url=url, meta=request_meta)
    return HtmlResponse(url=url, body=body, encoding="utf-8", request=request)


def paginated_body(page_count=6):
    return "".join(
        '<a class="next" href="https://example.com/page/{0}">page {0}</a>'.format(i)
        for i in range(2, page_count + 2)
    )


def queued_urls(to_traverse_links_list):
    return [link["link"] for link in to_traverse_links_list]


def run_traversals(stats_values=None, max_pages=3, body=None, meta=None, allowed_domains=None):
    spider = TraversalTestSpider(stats_values=stats_values)
    response = make_response(body or paginated_body(), meta=meta)
    spider_config = make_spider_config(max_pages=max_pages)
    return spider.run_traversals(spider_config=spider_config, response=response)


# ---------------------------------------------------------------------------
# Pagination count (max_pages)
# ---------------------------------------------------------------------------

def test_pagination_queues_up_to_max_pages_requests():
    """A page with more links than max_pages only queues max_pages requests."""
    _, to_traverse = run_traversals(stats_values={}, max_pages=3)

    assert len(to_traverse) == 3
    assert queued_urls(to_traverse) == [
        "https://example.com/page/2",
        "https://example.com/page/3",
        "https://example.com/page/4",
    ]
    assert [link["meta"]["current_request_traversal_count"] for link in to_traverse] == [0, 1, 2]


def test_pagination_budget_shrinks_with_already_committed_requests():
    """When two pagination requests were already committed, only one may be queued."""
    _, to_traverse = run_traversals(stats_values={TRAVERSAL_STATS_KEY: 2}, max_pages=3)

    assert len(to_traverse) == 1
    assert queued_urls(to_traverse) == ["https://example.com/page/2"]
    assert to_traverse[0]["meta"]["current_request_traversal_count"] == 2


def test_pagination_stops_when_budget_is_exhausted():
    """Once the committed request count reaches max_pages nothing more is queued."""
    _, to_traverse = run_traversals(stats_values={TRAVERSAL_STATS_KEY: 3}, max_pages=3)

    assert to_traverse == []


def test_pagination_ignores_requests_beyond_the_limit():
    """Counters above max_pages (e.g. retries counted twice) queue nothing."""
    _, to_traverse = run_traversals(stats_values={TRAVERSAL_STATS_KEY: 5}, max_pages=3)

    assert to_traverse == []


def test_pagination_single_page_max_pages():
    _, to_traverse = run_traversals(stats_values={}, max_pages=1)

    assert queued_urls(to_traverse) == ["https://example.com/page/2"]


def test_pagination_uses_default_max_pages_when_not_configured():
    """max_pages defaults to 1, so a traversal still cannot run unlimited."""
    spider = TraversalTestSpider(stats_values={})
    response = make_response(paginated_body())
    spider_config = make_spider_config()
    spider_config["traversals"][0].pop("max_pages")

    _, to_traverse = spider.run_traversals(spider_config=spider_config, response=response)

    assert len(to_traverse) == 1


def test_pagination_when_counter_is_missing_is_still_bounded():
    """A missing counter is treated as 0, but max_pages still applies."""
    spider = TraversalTestSpider(stats_values={})
    spider.crawler.stats.values.pop(TRAVERSAL_STATS_KEY, None)
    response = make_response(paginated_body())

    _, to_traverse = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=response)

    assert len(to_traverse) == 3
    assert queued_urls(to_traverse)[-1] == "https://example.com/page/4"


def test_pagination_counter_missing_when_crawler_has_no_stats():
    """Without any crawler/stats the counter falls back to 0, and max_pages is honoured."""
    spider = WebCrawlerBase.__new__(WebCrawlerBase)
    spider.crawler = SimpleNamespace(stats=None)
    assert spider.get_current_traversal_requests_count(TRAVERSAL_ID) == 0

    response = make_response(paginated_body())
    _, to_traverse = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=response)

    assert len(to_traverse) == 3


def test_pagination_does_not_count_links_of_the_current_page():
    """
    Regression: the counter used to be incremented inside the link loop, so
    every link of the same page saw the same stale value and all were queued.
    """
    _, to_traverse = run_traversals(stats_values={}, max_pages=3, body=paginated_body(page_count=50))

    assert len(to_traverse) == 3


def test_pagination_queued_meta_carries_incremented_counts():
    """Queued requests get distinct, increasing committed counts."""
    _, to_traverse = run_traversals(stats_values={TRAVERSAL_STATS_KEY: 1}, max_pages=3)

    assert [link["meta"]["current_request_traversal_count"] for link in to_traverse] == [1, 2]
    assert all(link["meta"]["current_traversal_max_count"] == 3 for link in to_traverse)
    assert all(link["meta"]["current_request_traversal_id"] == TRAVERSAL_ID for link in to_traverse)


def test_pagination_no_links_queues_nothing():
    _, to_traverse = run_traversals(
        stats_values={}, max_pages=3, body='<a href="https://example.com/other">other</a>')

    assert to_traverse == []


def test_pagination_budget_is_shared_across_pages():
    """
    The committed count is global to the traversal: after one page has been
    downloaded (counter == 1), a subsequent page can only queue the remaining
    two requests of a max_pages=3 budget.
    """
    spider = TraversalTestSpider(stats_values={TRAVERSAL_STATS_KEY: 1})
    response = make_response(
        paginated_body(),
        meta={"current_request_traversal_id": TRAVERSAL_ID},
    )

    _, to_traverse = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=response)

    assert len(to_traverse) == 2


def test_pagination_from_start_url_is_bounded():
    """A start-url response (traversal id 'init') still only queues max_pages."""
    _, to_traverse = run_traversals(
        stats_values={}, max_pages=3, meta={"current_request_traversal_id": "init"})

    assert len(to_traverse) == 3


# ---------------------------------------------------------------------------
# GenericLinkExtractor domain filtering
# ---------------------------------------------------------------------------

CROSS_SITE_BODY = b"""
<html><body>
  <a href="https://example.com/about">about</a>
  <a href="https://example.com/page/2">page 2</a>
  <a href="http://www.example.com/contact">contact</a>
  <a href="https://other.example.com/page/3">other example</a>
  <a href="https://evil.com/page/4">evil</a>
</body></html>
"""


def make_link_response(url="https://example.com/", body=CROSS_SITE_BODY):
    return HtmlResponse(url=url, body=body, encoding="utf-8")


def test_extractor_empty_allow_domains_keeps_only_same_domain_links():
    links = GenericLinkExtractor(allow_domains=[]).extract_links(
        response=make_link_response())

    assert set(links) == {
        "https://example.com/about",
        "https://example.com/page/2",
    }
    assert "https://evil.com/page/4" not in links
    assert "https://other.example.com/page/3" not in links


def test_extractor_unset_allow_domains_keeps_only_same_domain_links():
    links = GenericLinkExtractor().extract_links(response=make_link_response())

    assert "https://evil.com/page/4" not in links
    assert "https://example.com/about" in links


def test_extractor_empty_allow_domains_uses_response_domain():
    """On a non 'example.com' page, only that page's own domain is kept."""
    links = GenericLinkExtractor(allow_domains=[]).extract_links(
        response=make_link_response(url="https://evil.com/landing"))

    assert links == ["https://evil.com/page/4"]


def test_extractor_star_allow_domains_removes_the_restriction():
    links = GenericLinkExtractor(allow_domains=["*"]).extract_links(
        response=make_link_response())

    assert "https://evil.com/page/4" in links
    assert "https://example.com/about" in links
    assert len(links) == 5


def test_extractor_non_empty_allow_domains_keeps_existing_matching():
    """
    Historical (regex) matching is preserved for a non empty allow_domains:
    only links containing '/<domain>' are kept.
    """
    links = GenericLinkExtractor(allow_domains=["example.com"]).extract_links(
        response=make_link_response())

    assert set(links) == {
        "https://example.com/about",
        "https://example.com/page/2",
    }
    assert "https://evil.com/page/4" not in links


def test_extractor_multiple_allow_domains():
    links = GenericLinkExtractor(allow_domains=["evil.com", "other.example.com"]).extract_links(
        response=make_link_response())

    assert set(links) == {"https://evil.com/page/4", "https://other.example.com/page/3"}


def test_extractor_no_links_returns_empty_list():
    response = HtmlResponse(url="https://example.com/", body=b"<html><body>no links</body></html>",
                             encoding="utf-8")

    assert GenericLinkExtractor(allow_domains=[]).extract_links(response=response) == []


def test_traversal_does_not_follow_cross_site_links():
    """
    End to end: a traversal on a page linking to another site must not queue
    requests for the other site when the target spider has no allowed_domains.
    """
    body = (
        '<a class="next" href="https://example.com/page/2">2</a>'
        '<a class="next" href="https://evil.com/page/3">3</a>'
        '<a class="next" href="https://example.com/page/4">4</a>'
    )
    spider = TraversalTestSpider(stats_values={})
    response = make_response(body)
    # make_manifest() keeps allowed_domains empty on the target spider.
    _, to_traverse = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=response)

    assert queued_urls(to_traverse) == [
        "https://example.com/page/2",
        "https://example.com/page/4",
    ]
    assert "https://evil.com/page/3" not in queued_urls(to_traverse)


def test_traversal_star_allowed_domains_follows_cross_site_links():
    spider = TraversalTestSpider(stats_values={})
    body = (
        '<a class="next" href="https://example.com/page/2">2</a>'
        '<a class="next" href="https://evil.com/page/3">3</a>'
    )
    response = make_response(body)
    manifest = make_manifest(allowed_domains=["*"])
    response.request.meta["manifest"] = manifest

    _, to_traverse = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=response)

    assert queued_urls(to_traverse) == [
        "https://example.com/page/2",
        "https://evil.com/page/3",
    ]


def test_traversal_respects_configured_allowed_domains():
    spider = TraversalTestSpider(stats_values={})
    body = (
        '<a class="next" href="https://example.com/page/2">2</a>'
        '<a class="next" href="https://evil.com/page/3">3</a>'
    )
    response = make_response(body)
    response.request.meta["manifest"] = make_manifest(allowed_domains=["evil.com"])

    _, to_traverse = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=response)

    assert queued_urls(to_traverse) == ["https://evil.com/page/3"]


# ---------------------------------------------------------------------------
# run_traversal wiring
# ---------------------------------------------------------------------------

def test_run_traversal_passes_star_through_to_extractor():
    """'*' must reach the extractor as a wildcard, not be turned into []."""
    response = make_link_response()
    traversal = {
        "selector_type": "css",
        "selector_value": "a",
        "allow_domains": ["*"],
    }

    links = WebCrawlerBase.run_traversal(response=response, traversal=traversal)

    assert "https://evil.com/page/4" in links


def test_run_traversal_without_allow_domains_is_same_domain_only():
    response = make_link_response()
    traversal = {
        "selector_type": "css",
        "selector_value": "a",
    }

    links = WebCrawlerBase.run_traversal(response=response, traversal=traversal)

    assert "https://evil.com/page/4" not in links


def test_get_remaining_traversal_requests_helper():
    spider = TraversalTestSpider(stats_values={})

    assert spider.get_remaining_traversal_requests(0, 3) == 3
    assert spider.get_remaining_traversal_requests(2, 3) == 1
    assert spider.get_remaining_traversal_requests(3, 3) == 0
    assert spider.get_remaining_traversal_requests(None, 3) == 3
    assert spider.get_remaining_traversal_requests(0, None) == 1


# ---------------------------------------------------------------------------
# Middleware controllers
# ---------------------------------------------------------------------------

def test_ignore_traversal_requests_controller_ignores_over_limit_request():
    """
    Middleware-level guard: with 3 requests already committed for a traversal
    capped at 3, the next (4th) request is refused.
    """
    from scrapy.exceptions import IgnoreRequest
    from crawlerflow.contrib.middlewares.downloaders.controllers import (
        IgnoreTraversalRequestsController,
    )

    spider = TraversalTestSpider(stats_values={TRAVERSAL_STATS_KEY: 3})
    request = scrapy.Request(
        "https://example.com/page/5",
        meta={
            "current_request_traversal_id": TRAVERSAL_ID,
            "current_traversal_max_count": 3,
        },
    )

    with pytest.raises(IgnoreRequest):
        IgnoreTraversalRequestsController().process_request(request, spider)


def test_ignore_traversal_requests_controller_allows_request_within_limit():
    from crawlerflow.contrib.middlewares.downloaders.controllers import (
        IgnoreTraversalRequestsController,
    )

    spider = TraversalTestSpider(stats_values={TRAVERSAL_STATS_KEY: 2})
    request = scrapy.Request(
        "https://example.com/page/3",
        meta={
            "current_request_traversal_id": TRAVERSAL_ID,
            "current_traversal_max_count": 3,
        },
    )

    assert IgnoreTraversalRequestsController().process_request(request, spider) is None


def test_ignore_traversal_requests_controller_allows_last_request_of_budget():
    from crawlerflow.contrib.middlewares.downloaders.controllers import (
        IgnoreTraversalRequestsController,
    )

    spider = TraversalTestSpider(stats_values={TRAVERSAL_STATS_KEY: 2})
    request = scrapy.Request(
        "https://example.com/page/3",
        meta={
            "current_request_traversal_id": TRAVERSAL_ID,
            "current_traversal_max_count": 3,
        },
    )

    # third and last request of the budget (counter goes 2 -> 3)
    assert IgnoreTraversalRequestsController().process_request(request, spider) is None


# ---------------------------------------------------------------------------
# Stats middleware: the committed counter must be incremented exactly once
# ---------------------------------------------------------------------------

def test_traversal_requests_count_incremented_once_per_request():
    from crawlerflow.contrib.middlewares.downloaders.spider_analytics import (
        IndividualSpiderRequestStats,
        IndividualSpiderResponseStats,
    )

    spider = TraversalTestSpider(stats_values={})
    spider.spider_config = {"spider_id": "source_spider"}
    request = scrapy.Request(
        "https://example.com/page/2",
        meta={"current_request_traversal_id": TRAVERSAL_ID},
    )
    response = HtmlResponse(url=request.url, request=request, body=b"<html></html>", encoding="utf-8")

    IndividualSpiderRequestStats().process_request(request, spider)
    IndividualSpiderResponseStats().process_response(request, response, spider)

    assert spider.crawler.stats.values[TRAVERSAL_STATS_KEY] == 1


def test_traversal_counter_drives_the_page_budget():
    """
    Three committed requests (count == max_pages) leave no budget, so a fourth
    page is never queued.
    """
    from crawlerflow.contrib.middlewares.downloaders.spider_analytics import (
        IndividualSpiderRequestStats,
    )

    spider = TraversalTestSpider(stats_values={})
    spider.spider_config = {"spider_id": "source_spider"}
    for page in range(2, 5):
        IndividualSpiderRequestStats().process_request(
            scrapy.Request("https://example.com/page/{}".format(page),
                           meta={"current_request_traversal_id": TRAVERSAL_ID}),
            spider,
        )
    assert spider.crawler.stats.values[TRAVERSAL_STATS_KEY] == 3

    response = make_response(paginated_body())
    _, to_traverse = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=response)

    assert to_traverse == []


# ---------------------------------------------------------------------------
# End to end: crawl simulation through the real middlewares
# ---------------------------------------------------------------------------

def test_crawl_issues_at_most_max_pages_traversal_requests():
    """
    Simulate the downloader middleware chain page by page: every queued request
    passes IgnoreTraversalRequestsController before IndividualSpiderRequestStats
    increments the counter, exactly like a running spider. With max_pages=3 and
    a start page holding 10 pagination links, only 3 traversal requests are
    ever issued.
    """
    from scrapy.exceptions import IgnoreRequest
    from crawlerflow.contrib.middlewares.downloaders.controllers import (
        IgnoreTraversalRequestsController,
    )
    from crawlerflow.contrib.middlewares.downloaders.spider_analytics import (
        IndividualSpiderRequestStats,
    )

    spider = TraversalTestSpider(stats_values={})
    spider.spider_config = {"spider_id": "source_spider"}
    controller = IgnoreTraversalRequestsController()
    stats_middleware = IndividualSpiderRequestStats()

    issued_requests = []
    # start page is handled by parse() directly: no middleware gate for it
    queue = [(make_response(paginated_body(page_count=10)), None)]

    while queue:
        response, request = queue.pop(0)
        if request is not None:
            try:
                controller.process_request(request, spider)
            except IgnoreRequest:
                continue
            stats_middleware.process_request(request, spider)
            issued_requests.append(request.url)

        _, to_traverse = spider.run_traversals(
            spider_config=make_spider_config(max_pages=3), response=response)
        for to_traverse_link in to_traverse:
            meta = dict(to_traverse_link["meta"])
            meta["manifest"] = make_manifest()
            meta["spider_config"] = make_spider_config(max_pages=3)
            next_request = Request(to_traverse_link["link"], meta=meta)
            next_response = HtmlResponse(
                url=next_request.url, request=next_request,
                body=paginated_body(page_count=10), encoding="utf-8")
            queue.append((next_response, next_request))

    assert len(issued_requests) == 3
    assert issued_requests == [
        "https://example.com/page/2",
        "https://example.com/page/3",
        "https://example.com/page/4",
    ]


def test_extractor_same_domain_is_case_insensitive_and_ignores_ports():
    body = (
        '<a href="https://EXAMPLE.com/a">a</a>'
        '<a href="https://example.com:8443/b">b</a>'
        '<a href="https://evil.com/c">c</a>'
    )
    response = HtmlResponse(url="https://Example.com:443/", body=body, encoding="utf-8")

    links = GenericLinkExtractor(allow_domains=[]).extract_links(response=response)

    # scrapy's extractor lower cases the host when building the url
    assert links == ["https://example.com/a", "https://example.com:8443/b"]


def test_full_budget_is_not_queued_again_on_a_later_page():
    """
    The budget is derived from the committed request counter, so once the
    counter reaches max_pages a later page queues nothing, regardless of how
    many traversal links it holds.
    """
    spider = TraversalTestSpider(stats_values={TRAVERSAL_STATS_KEY: 3})

    page_two_response = make_response(
        paginated_body(page_count=10),
        url="https://example.com/page/2",
        meta={"current_request_traversal_id": TRAVERSAL_ID},
    )
    _, to_traverse = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=page_two_response)

    assert to_traverse == []


def test_budget_accumulates_across_pages():
    """
    When the start page holds fewer links than the budget, following pages can
    keep paging, but the total never exceeds max_pages requests.
    """
    spider = TraversalTestSpider(stats_values={})

    single_link_response = make_response(
        '<a class="next" href="https://example.com/page/2">2</a>')
    _, first_batch = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3), response=single_link_response)
    assert len(first_batch) == 1

    # the single queued request is downloaded, so its stats counter is now 1
    spider.crawler.stats.set_value(TRAVERSAL_STATS_KEY, 1)
    _, second_batch = spider.run_traversals(
        spider_config=make_spider_config(max_pages=3),
        response=make_response(
            paginated_body(page_count=10),
            url="https://example.com/page/2",
            meta={"current_request_traversal_id": TRAVERSAL_ID},
        ))

    assert len(second_batch) == 2
    assert len(first_batch) + len(second_batch) == 3


def test_budgets_are_tracked_per_traversal_id():
    """Request counts are tracked per traversal id, not globally."""
    spider = TraversalTestSpider(stats_values={
        TRAVERSAL_STATS_KEY: 3,
        "crawlerflow-stats/traversals/other_traversal/requests_count": 0,
    })
    spider_config = make_spider_config(max_pages=3)
    other_traversal = dict(spider_config["traversals"][0])
    other_traversal["traversal_id"] = "other_traversal"
    spider_config["traversals"].append(other_traversal)

    response = make_response(paginated_body())
    _, to_traverse = spider.run_traversals(spider_config=spider_config, response=response)

    # the exhausted traversal queues nothing, the fresh one gets a full budget
    assert len(to_traverse) == 3
    assert {link["meta"]["current_request_traversal_id"] for link in to_traverse} == {"other_traversal"}
