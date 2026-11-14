"""Tests for the structured batch outcome returned by CrawlEngine.start().

All tests use a scripted FakePageGrabber; no real network access happens.
"""

from collections import defaultdict
from threading import Lock

from crawler.crawler import CrawlEngine
from crawler.libs.internals.crawler_exceptions import CrawlerException
from crawler.libs.internals.web_objects import URL, WebPage

START = "https://example.com/"
PAGE_A = "https://example.com/a"
PAGE_B = "https://example.com/b"
PAGE_C = "https://example.com/c"
FLAKY = "https://example.com/flaky"
IMAGE = "https://example.com/logo.png"


def html_with_links(*links):
    anchors = "".join('<a href="{0}">x</a>'.format(link) for link in links)
    return "<html><body>{0}</body></html>".format(anchors)


class FakePageGrabber(object):
    """A scripted drop-in replacement for PageGrabber (no network).

    script maps a link to one of:
      ("ok", html)   -> GET returns a textual WebPage with the given html
      ("fail",)      -> GET always raises CrawlerException (code 102)
      ("nontext",)   -> HEAD raises CrawlerException (code 101), like a binary file
    """

    USER_AGENT = ("FakeBot", "fake-agent")

    def __init__(self, script):
        self.script = script
        self.get_counts = defaultdict(int)
        self._lock = Lock()

    def get_page(self, url, verb="GET", use_referer=False):
        action = self.script[url.LINK][0]

        if verb.lower() == "head":
            if action == "nontext":
                raise CrawlerException(message="LINK'S BODY DOES NOT HAVE A VALID TYPE", code="101")
            return WebPage(text="", link=url.LINK,
                           headers={"content-type": "text/html"}, status_code=200)

        with self._lock:
            self.get_counts[url.LINK] += 1

        if action == "fail":
            raise CrawlerException(message="COULD NOT RETIEVE LINK'S DATA", code="102")
        if action == "nontext":
            raise CrawlerException(message="LINK'S BODY DOES NOT HAVE A VALID TYPE", code="101")
        return WebPage(text=self.script[url.LINK][1], link=url.LINK,
                       headers={"content-type": "text/html"}, status_code=200)


def make_engine(script, max_depth=3, max_failure_threshold=1):
    ce = CrawlEngine()
    ce.start_url = URL(link=START, depth=0, parent=None)
    ce.start_domain = "example.com"
    ce.pg = FakePageGrabber(script)
    ce.max_depth = max_depth
    ce.max_failure_threshold = max_failure_threshold
    # keep the test fast without changing crawl semantics
    ce.max_sleep_time = 0
    ce.empty_queue_wait = 0.05
    ce.max_empty_queue_threshold = 10
    ce.thread_sleep_time = 0.01
    return ce


def assert_disjoint(outcome):
    categories = list(outcome.values())
    for i, first in enumerate(categories):
        for second in categories[i + 1:]:
            assert first.isdisjoint(second), "a link appears in more than one category"


def test_successful_crawl_reports_crawled_pages():
    ce = make_engine({
        START: ("ok", html_with_links(PAGE_A, PAGE_B)),
        PAGE_A: ("ok", html_with_links(PAGE_C)),
        PAGE_B: ("ok", html_with_links()),
        PAGE_C: ("ok", html_with_links()),
    })

    outcome = ce.start()

    assert outcome["crawled_pages"] == {START, PAGE_A, PAGE_B, PAGE_C}
    assert outcome["missed_pages"] == set()
    assert outcome["too_deep_pages"] == set()
    assert outcome["garbage_pages"] == set()
    assert_disjoint(outcome)


def test_retries_exhausted_page_is_reported_as_missed():
    ce = make_engine({
        START: ("ok", html_with_links(FLAKY)),
        FLAKY: ("fail",),
    }, max_failure_threshold=1)

    outcome = ce.start()

    assert outcome["crawled_pages"] == {START}
    assert outcome["missed_pages"] == {FLAKY}
    assert outcome["too_deep_pages"] == set()
    assert outcome["garbage_pages"] == set()
    assert_disjoint(outcome)

    # retry semantics preserved: the URL is re-queued while
    # FAILURES <= max_failure_threshold and only then lands in missed_pages,
    # i.e. max_failure_threshold + 2 GET attempts in total
    assert ce.pg.get_counts[FLAKY] == ce.max_failure_threshold + 2


def test_links_beyond_max_depth_are_recorded_not_dropped():
    ce = make_engine({
        START: ("ok", html_with_links(PAGE_A, PAGE_B)),
        PAGE_A: ("ok", html_with_links()),
        PAGE_B: ("ok", html_with_links()),
    }, max_depth=0)

    outcome = ce.start()

    assert outcome["crawled_pages"] == {START}
    assert outcome["too_deep_pages"] == {PAGE_A, PAGE_B}
    assert outcome["missed_pages"] == set()
    assert outcome["garbage_pages"] == set()
    assert_disjoint(outcome)


def test_non_textual_page_is_reported_as_garbage():
    ce = make_engine({
        START: ("ok", html_with_links(IMAGE)),
        IMAGE: ("nontext",),
    })

    outcome = ce.start()

    assert outcome["crawled_pages"] == {START}
    assert outcome["garbage_pages"] == {IMAGE}
    assert outcome["missed_pages"] == set()
    assert outcome["too_deep_pages"] == set()
    assert_disjoint(outcome)


def test_outcome_is_complete_when_start_returns():
    ce = make_engine({
        START: ("ok", html_with_links(PAGE_A, FLAKY, IMAGE)),
        PAGE_A: ("ok", html_with_links(PAGE_B)),
        PAGE_B: ("ok", html_with_links()),
        FLAKY: ("fail",),
        IMAGE: ("nontext",),
    }, max_depth=1)

    outcome = ce.start()

    # no worker thread of this batch may still be running
    assert all(not t.is_alive() for t in ce._worker_threads)
    # the returned object is also exposed on the engine
    assert outcome is ce.last_outcome
    # the outcome already reflects everything the workers did
    assert outcome["crawled_pages"] == {START, PAGE_A}
    assert outcome["missed_pages"] == {FLAKY}
    assert outcome["garbage_pages"] == {IMAGE}
    assert outcome["too_deep_pages"] == {PAGE_B}
    assert_disjoint(outcome)
