import asyncio
import aiohttp
import async_timeout
import logging
import time
from typing import Optional
from spider.utils import normalize_url
from spider.link_finder import LinkFinder
from spider.plugin import PluginManager
from spider.storage import save_page

class Spider:
    def __init__(self, start_url: str, config: dict, plugin_manager: Optional[PluginManager] = None) -> None:
        """
        Initialize the Spider.

        :param start_url: The starting URL for crawling.
        :param config: Configuration dictionary.
        :param plugin_manager: Optional PluginManager for processing pages.
        """
        self.config = config
        # All URLs are normalized once here and again whenever they enter the
        # queue, so the start URL and links discovered later are compared in the
        # same canonical form (e.g. "http://example.com/" == "http://example.com").
        self.start_url = normalize_url(start_url)
        # URLs that have been claimed for processing.
        self.visited = set()
        # Every URL ever enqueued (queued, in flight, or done). This makes
        # enqueueing idempotent: the same URL is only queued once per crawl.
        self._enqueued = set()
        self.to_visit: asyncio.Queue[str] = asyncio.Queue()
        self.plugin_manager = plugin_manager if plugin_manager else PluginManager()
        # Limit concurrent connections.
        self.semaphore = asyncio.Semaphore(10)
        # Serializes the start of requests so rate_limit stays a global (not
        # per-worker) delay between requests.
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0
        # Seed the queue through the same path used for discovered links so the
        # start URL obeys exactly the same normalization/deduplication rules.
        self._enqueue(self.start_url)

    def _claim(self, normalized_url: str) -> bool:
        """
        Atomically mark a URL as visited.

        There is no await between the membership check and the insertion, so
        concurrent tasks in the event loop cannot both claim the same URL.

        :param normalized_url: The normalized URL to claim.
        :return: True if this call claimed the URL, False if it was already claimed.
        """
        if normalized_url in self.visited:
            return False
        self.visited.add(normalized_url)
        return True

    def _enqueue(self, url: str) -> bool:
        """
        Normalize and enqueue a URL at most once per crawl.

        This method is synchronous on purpose: the check-and-insert into
        ``_enqueued`` is atomic with respect to other coroutines, so two pages
        that discover the same link concurrently cannot enqueue it twice.

        :param url: The URL to enqueue.
        :return: True if the URL was enqueued, False if it was a duplicate.
        """
        normalized_url = normalize_url(url)
        if normalized_url in self._enqueued or normalized_url in self.visited:
            return False
        self._enqueued.add(normalized_url)
        self.to_visit.put_nowait(normalized_url)
        return True

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """
        Fetch a URL asynchronously.

        :param session: The aiohttp session.
        :param url: The URL to fetch.
        :return: The response text if successful; None otherwise.
        """
        try:
            async with async_timeout.timeout(self.config['timeout']):
                headers = {'User-Agent': self.config['user_agent']}
                async with session.get(url, headers=headers) as response:
                    if response.status == 200 and 'text/html' in response.headers.get('Content-Type', ''):
                        return await response.text()
                    else:
                        logging.warning(f"Skipping URL {url}: status {response.status} or invalid content type")
                        return None
        except Exception as e:
            logging.error(f"Exception fetching {url}: {e}")
            return None

    async def _respect_rate_limit(self) -> None:
        """
        Wait until at least ``rate_limit`` seconds have passed since the previous
        request started. Holding the lock from the timestamp read through the
        sleep guarantees the delay is global rather than per-worker.
        """
        rate_limit = float(self.config.get('rate_limit') or 0)
        if rate_limit <= 0:
            return
        async with self._rate_lock:
            now = time.monotonic()
            wait = self._last_request_at + rate_limit - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def process_url(self, session: aiohttp.ClientSession, url: str) -> None:
        """
        Process a single URL: fetch, process via plugins, save, and extract further links.

        Links are discovered in the content *after* the plugin chain has run, so
        pages rendered or expanded by a plugin contribute their new links to the
        crawl. If the plugin chain fails for a page, the unprocessed content is
        returned by the manager and is what gets saved and mined for links.

        :param session: The aiohttp session.
        :param url: The URL to process.
        """
        normalized_url = normalize_url(url)
        if not self._claim(normalized_url):
            return
        logging.info(f"Processing {normalized_url}")
        await self._respect_rate_limit()
        async with self.semaphore:
            content = await self.fetch(session, normalized_url)
        if content:
            # Process the content via plugins. The manager logs any failure with
            # the plugin name and URL, and falls back to the original content.
            processed_content = await self.plugin_manager.run_plugins(normalized_url, content)
            # Save the final content in the database.
            save_page(normalized_url, processed_content)
            # Extract and enqueue links from the content that was actually saved
            # and processed, not from the raw response.
            finder = LinkFinder(normalized_url, normalized_url)
            finder.feed(processed_content)
            for link in finder.page_links():
                self._enqueue(link)

    async def _worker(self, session: aiohttp.ClientSession) -> None:
        """
        Consume URLs from the queue until it is exhausted and all in-flight work
        has been marked done.

        :param session: The aiohttp session.
        """
        while True:
            url = await self.to_visit.get()
            try:
                await self.process_url(session, url)
            except Exception as e:
                # Never let a single page kill a worker: otherwise task_done()
                # is skipped and crawl() would wait forever.
                logging.error(f"Error processing {url}: {e}", exc_info=True)
            finally:
                self.to_visit.task_done()

    async def crawl(self) -> None:
        """
        Begin the crawling process.

        Workers keep draining the queue while pages are still discovering new
        links, so links added after the initial burst are not dropped.
        """
        worker_count = max(1, int(self.config.get('threads', 10) or 10))
        async with aiohttp.ClientSession() as session:
            workers = [
                asyncio.create_task(self._worker(session))
                for _ in range(worker_count)
            ]
            try:
                # join() only returns once every queued URL has been processed,
                # including URLs enqueued while the crawl was running.
                await self.to_visit.join()
            finally:
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
