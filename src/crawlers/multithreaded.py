from concurrent.futures import ThreadPoolExecutor
from queue import Empty
import json
import os
import time

from typing import Callable, Optional

from playwright.sync_api import sync_playwright, Response, TimeoutError


from .base import (
    Crawler,
    FAILURE_EXCEPTION,
    FAILURE_HTTP_STATUS,
    FAILURE_REDIRECT_OUT_OF_SCOPE,
)
from utils.url import add_base_url, get_base_url, get_cleaned_url, get_filename, is_url

# number of threads for crawling
NUM_THREADS = 8

# milliseconds to wait for 'networkidle'
PAGE_WAIT_TIMEOUT = 10_000.0

# seconds to wait before checking the queue again while workers are busy
QUEUE_POLL_INTERVAL = 0.1


class MultithreadedCrawler(Crawler):
    def __init__(
        self,
        seed_url: str,
        output_dir: Optional[str] = None,
        done_callback: Optional[Callable] = None,
        num_threads=NUM_THREADS,
    ):
        super().__init__(seed_url, output_dir, done_callback)

        # number of threads this crawler should use
        self.num_threads = num_threads

    # method to initiate crawling
    def start(self):
        start_time = time.time()

        try:
            # the seed has to be resolved before anything else so that, if it
            # redirects to another site, the crawl scope is settled before any
            # other URL is handed to a worker
            self.put_seed_first()

            with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
                futures = set()
                while True:
                    try:
                        url = self.url_queue.get(timeout=QUEUE_POLL_INTERVAL)
                    except Empty:
                        # forget about workers that already finished; if none are
                        # left and the queue is still empty, the crawl is done
                        futures = {future for future in futures if not future.done()}
                        if not futures and self.url_queue.empty():
                            break
                        continue

                    # skip this URL if it's out of scope (e.g. it belongs to the
                    # old site after the seed redirected) or already handled
                    if not self.claim_url(url):
                        continue

                    # sechedule the crawl method for this URL
                    future = executor.submit(self.crawl, url)
                    futures.add(future)

                    if url == self.seed_request_url:
                        # wait for the seed to settle, so a cross-domain
                        # redirect drops old URLs from the queue before they
                        # can be distributed
                        try:
                            future.result()
                        except Exception as e:
                            self.logger.error(f"seed crawl failed - {e}")

            if callable(self.done_callback):
                self.done_callback()
        except Exception as e:
            self.logger.critical(f"start error: {e}")
        finally:
            with self.lock:
                # the failed list is a list of dicts, one per failure:
                # {"url": ..., "final_url": ..., "reason": ..., "status"/"message": ...}
                self.metadata["failed"] = [
                    dict(entry)
                    for entry in sorted(
                        self.failures,
                        key=lambda entry: (
                            str(entry.get("url")),
                            str(entry.get("final_url")),
                            str(entry.get("reason")),
                        ),
                    )
                ]

            self.logger.info(
                f"downloaded {len(self.metadata['successful'])} pages to '{self.output_dir}' pages in {round(time.time() - start_time, 2)} seconds"
            )

            if len(self.metadata["failed"]) > 0:
                self.logger.warning(
                    f"failed to crawl {len(self.metadata['failed'])} URLs"
                )

            # save the metadata as a JSON file
            with open(os.path.join(self.output_dir, "metadata.json"), "w") as json_file:
                json.dump(self.metadata, json_file, indent=2)

    # function to crawl a URL
    def crawl(self, url: str):
        url = get_cleaned_url(url)
        if url is None:
            raise ValueError(f"invalid URL '{url}' somehow reached crawl method")

        current_url = url
        self.logger.info(f"crawling {current_url}")

        # launch the Chrome browser in headless mode
        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch()
                page = browser.new_page()

                response = None  # main resource response i.e response of the page
                try:
                    response = page.goto(
                        url, wait_until="networkidle", timeout=PAGE_WAIT_TIMEOUT
                    )
                except TimeoutError:
                    self.logger.warning(
                        f"{current_url} timeout - using whatever's rendered"
                    )

                if not is_url(page.url):
                    raise ValueError(f"invalid page url '{page.url}'")

                # in case of redirects, current URL would be different from the given URL
                current_url = get_cleaned_url(page.url)

                seed_redirected = url == self.seed_request_url and current_url != url

                if seed_redirected:
                    # the seed itself redirected, so the crawl scope has to move
                    # with it before any other URL is followed
                    self.handle_seed_redirect(current_url)
                elif not self.is_in_scope(url):
                    # this URL belonged to the old site and was already in flight
                    # when the seed redirected; its page must not be written
                    self.logger.info(
                        f"dropping {current_url} as it is outside of the current crawl scope '{self.seed_url}'"
                    )
                    return
                elif url != current_url and not self.is_in_scope(current_url):
                    # the page redirected outside of the crawl scope; that is a
                    # failure, not something to silently skip
                    self.record_failure(
                        url, FAILURE_REDIRECT_OUT_OF_SCOPE, final_url=current_url
                    )
                    self.logger.warning(
                        f"skipping {current_url} as it redirected outside of the crawl scope '{self.seed_url}'"
                    )
                    return

                if isinstance(response, Response) and response.status >= 400:
                    self.record_failure(
                        url,
                        FAILURE_HTTP_STATUS,
                        final_url=current_url,
                        status=response.status,
                    )
                    self.logger.warning(
                        f"skipping {current_url} as it responded with status {response.status}"
                    )
                    return

                if (
                    not seed_redirected
                    and url != current_url
                    and not self.claim_url(current_url)
                ):
                    # the redirect target was already handled by another worker
                    return

                # save the rendered HTML to a file
                html = page.inner_html("html")
                file_path = os.path.join(
                    self.output_dir,
                    f"{get_filename(current_url, skip_base_url=True)}.html",
                )

                with open(file_path, "w") as html_file:
                    html_file.write(html)

                # mark both current & redirected URLs as crawled
                with self.lock:
                    self.crawled_urls.add(current_url)
                    self.crawled_urls.add(url)

                    # save current_url & file_name in metadata
                    self.metadata["successful"].append(
                        {"url": current_url, "filename": file_path}
                    )

                try:
                    # find all <a> tags
                    links = page.query_selector_all("a[href]")

                    # resolve hrefs against the page's own URL, so a page that
                    # was fetched from the old site can never produce links
                    # pointing at the new site just because the scope moved
                    hrefs = set(
                        get_cleaned_url(
                            add_base_url(link.get_attribute("href"), current_url)
                        )
                        for link in links
                    )

                    hrefs.discard(None)

                    # filter out URLs that should't be crawled
                    hrefs = filter(lambda href: self.should_crawl(href), hrefs)

                    count = 0
                    # add new URLs to the queue for crawling
                    for href in hrefs:
                        self.url_queue.put(href)
                        count += 1

                    self.logger.debug(
                        f"{current_url} -> found {len(links)} <a> tags, to crawl {count} URLs"
                    )
                except Exception as e:
                    # the page itself was downloaded successfully; failing to
                    # collect links is not a crawl failure for this URL
                    self.logger.warning(
                        f"error collecting links from {current_url} - {e}"
                    )
            except Exception as e:
                self.record_failure(
                    url,
                    FAILURE_EXCEPTION,
                    final_url=current_url,
                    message=f"{type(e).__name__}: {e}",
                )
                self.logger.error(f"error for url {current_url} - {e}", exc_info=True)
            finally:
                # close the browser to free up resources
                if browser is not None:
                    browser.close()

    def put_seed_first(self):
        """
        Put the seed URL at the front of the queue.

        The seed is normally the only pending URL at the start of a crawl, but
        this keeps the seed-first guarantee if a caller pre-filled the queue.
        """

        pending = []
        seed = self.seed_request_url

        while True:
            try:
                url = self.url_queue.get_nowait()
            except Empty:
                break

            if url != seed:
                pending.append(url)
            self.url_queue.task_done()

        self.url_queue.put(seed)
        for url in pending:
            self.url_queue.put(url)

    def handle_seed_redirect(self, final_url: str):
        """
        Move the crawl scope to the site the seed redirected to.

        Pending URLs that were built against the old site are dropped from the
        queue so they are never crawled, while pages that were already crawled
        (or already handed to a worker) stay untouched and remain in the
        results. Links discovered afterwards are queued under the new scope.
        """

        with self.lock:
            self.seed_url = get_base_url(final_url)
            self.base_url = get_base_url(final_url)
            self.distrubuted_urls.add(final_url)

            dropped = self.drop_out_of_scope_queued_urls()

        self.logger.info(
            f"seed redirected to {final_url}; crawl scope is now '{self.seed_url}'"
            f" and {dropped} queued URL(s) outside of it were dropped"
        )
