import logging
import os
from queue import Empty, Queue
import sys
import threading

from typing import Callable, Dict, List, Optional


from utils.url import get_base_url, get_cleaned_url, get_filename, is_url

# where to store the downloaded pages by default
DEFAULT_OUTPUT_DIR = os.path.join("web-wanderer", "downloads")

# reasons a URL can end up in the failed list
FAILURE_HTTP_STATUS = "http_status_error"
FAILURE_EXCEPTION = "crawl_exception"
FAILURE_REDIRECT_OUT_OF_SCOPE = "redirect_out_of_scope"


def is_within_scope(url: str, scope: str):
    """
    Returns True if `url` falls under `scope`.

    Scope is matched on path boundaries, not on raw string prefixes: with a
    scope of "http://a.test" a URL of "http://a.test.example/x" must not
    qualify just because its string starts with the same characters.
    """

    if not url or not scope:
        return False

    return url == scope or url.startswith(scope.rstrip("/") + "/")


class Crawler:
    def __init__(
        self,
        seed_url: str,
        output_dir: Optional[str] = None,
        done_callback: Optional[Callable] = None,
    ):
        if not is_url(seed_url):
            raise ValueError(f"invalid seed_url '{seed_url}'")

        # the url to start crawling from
        self.seed_url = get_cleaned_url(seed_url)

        # the url the crawl was originally requested with; kept around so
        # that a redirect of the seed can be told apart from ordinary pages
        # even after the crawl scope has been rewritten
        self.seed_request_url = self.seed_url

        # the base url of the seed
        self.base_url = get_base_url(seed_url)

        # where to store the downloaded pages
        self.output_dir = os.path.join(
            output_dir or DEFAULT_OUTPUT_DIR, get_filename(self.base_url)
        )

        # where the downloaded pages are to be stored
        os.makedirs(self.output_dir, exist_ok=True)

        # shared queue to store URLs to be crawled
        self.url_queue = Queue()

        # guards the shared state below (URL sets, crawl scope, metadata)
        self.lock = threading.RLock()

        # set to keep track of crawled URLs
        self.crawled_urls = set()

        # set of URLs that weren't crawled successfully
        self.failed_urls = set()

        # set of URLs that are already distributed to a worker
        self.distrubuted_urls = set()

        # structured failures, one dict per failed URL:
        # {"url": "<ORIGINAL URL>", "final_url": "<FINAL URL>", "reason": "<REASON>", ...}
        self.failures: List[Dict] = []

        # keys of failures already recorded, used to avoid duplicates
        self.failure_keys = set()

        # a callback function that will be called after crawling is SUCCESSFULLY done
        self.done_callback = done_callback

        # instantiate a Logger for this class
        self.logger = self.get_logger()

        # a JSON array of metadata [{"url": "<URL>", "filename": "<FILENAME>"}]
        self.metadata: Dict = {"successful": [], "failed": []}

    def get_logger(self):
        """
        Returns a Logger instance with this class's name.
        """

        logger = logging.getLogger(self.__class__.__name__)
        logger.setLevel(logging.DEBUG)

        # create console handler
        ch = logging.StreamHandler(sys.stdout)

        # create formatter for console handler
        formatter = logging.Formatter(
            fmt="%(asctime)s %(name)s (%(levelname)s) %(message)s",
            datefmt="%d-%b-%y %H:%M:%S",
        )
        ch.setFormatter(formatter)

        logger.addHandler(ch)
        return logger

    def start(self):
        raise NotImplementedError

    def is_in_scope(self, url: str):
        """
        Returns True if the given URL belongs to the current crawl scope.

        The scope is anchored to `seed_url`, which is rewritten when the seed
        itself redirects to another site. URLs belonging to the previous site
        therefore stop qualifying as soon as the scope moves.
        """

        url = get_cleaned_url(url)
        if url is None:
            return False

        with self.lock:
            return is_within_scope(url, self.seed_url)

    def should_crawl(self, url: str):
        """
        Returns True if the given URL
            - is in the current crawl scope
            - AND NOT already distrubuted
            - AND NOT already crawled
            - AND NOT failed\n
        else False.
        """

        url = get_cleaned_url(url)
        if url is None:
            return False

        with self.lock:
            return (
                is_within_scope(url, self.seed_url)
                and url not in self.distrubuted_urls
                and url not in self.crawled_urls
                and url not in self.failed_urls
            )

    def claim_url(self, url: str):
        """
        Atomically check that `url` may still be crawled and mark it as
        distributed. Returns False when it was already distributed, crawled
        or failed (e.g. two URLs redirecting to the same page, or another
        worker handling it first).
        """

        url = get_cleaned_url(url)
        if url is None:
            return False

        with self.lock:
            if (
                is_within_scope(url, self.seed_url)
                and url not in self.distrubuted_urls
                and url not in self.crawled_urls
                and url not in self.failed_urls
            ):
                self.distrubuted_urls.add(url)
                return True

            return False

    def record_failure(
        self,
        url: str,
        reason: str,
        final_url: Optional[str] = None,
        status: Optional[int] = None,
        message: Optional[str] = None,
    ):
        """
        Record a failed crawl in a structured way.

        `url` is the URL as it was handed to the worker, `final_url` is where
        the page actually ended up (which equals `url` when there was no
        redirect). The original/final URL pair and the reason are written to
        metadata.json's `failed` entries.
        """

        url = get_cleaned_url(url)
        final_url = get_cleaned_url(final_url) if final_url is not None else url

        with self.lock:
            key = (url, final_url, reason)
            if key in self.failure_keys:
                return

            self.failure_keys.add(key)
            self.failed_urls.add(url)
            self.failed_urls.add(final_url)

            entry = {"url": url, "final_url": final_url, "reason": reason}
            if status is not None:
                entry["status"] = status
            if message is not None:
                entry["message"] = str(message)

            self.failures.append(entry)

    def drop_out_of_scope_queued_urls(self):
        """
        Remove URLs that are no longer in scope from the pending queue.

        Called when the seed redirects to another site: the queue may still
        hold URLs that were built against the previous site, and those must
        never be crawled once the scope has moved. URLs already handed to a
        worker are untouched, so pages that are already downloaded (or are
        being downloaded) stay in the results.
        """

        dropped = 0
        kept = []

        while True:
            try:
                url = self.url_queue.get_nowait()
            except Empty:
                break

            if self.is_in_scope(url):
                kept.append(url)
            else:
                dropped += 1

            self.url_queue.task_done()

        for url in kept:
            self.url_queue.put(url)

        return dropped
