"""
WARNING:

Please make sure you install the bot with `pip install -e .` in order to get all the dependencies
on your Python environment.

Also, if you are using PyCharm or another IDE, make sure that you use the SAME Python interpreter
as your IDE.

If you get an error like:
```
ModuleNotFoundError: No module named 'botcity'
```

This means that you are likely using a different Python interpreter than the one used to install the bot.
To fix this, you can either:
- Use the same interpreter as your IDE and install your bot with `pip install -e .`
- Use the same interpreter as the one used to install the bot (`pip install -e .`)

Please refer to the documentation for more information at https://documentation.botcity.dev/
"""

"""
********************************************************************************
*                                   WARNING                                    *
********************************************************************************

You are using an outdated version of the template which is no longer recommended.
While this template works and is valid, it is strongly recommended to create a new project with the latest template version.

Please refer to our documentation: https://documentation.botcity.dev/tutorials/python-automations/desktop/
"""

"""
Configurable site crawler bot.

The crawl is configured through environment variables:

- BOT_START_URL    (required) URL where the crawl starts.
- BOT_MAX_PAGES    (required) maximum number of pages to visit; must be a
                   positive integer.
- BOT_OUTPUT_CSV   (optional) path of the CSV results file. Defaults to
                   `crawl_results.csv` in the project directory.
- BOT_PAGE_TIMEOUT (optional) page load timeout in seconds. Defaults to 30.

Every visited page is appended to the CSV immediately (one row per page with
columns: url, title, links, status). Pages already present in the CSV are
skipped on rerun, so a restarted crawl never duplicates completed pages.
Pages that time out or fail during extraction are recorded with a
`failed: <reason>` status and the crawl continues with the next page.
"""

import csv
import os
import sys
from collections import deque
from dataclasses import dataclass
from urllib.parse import urldefrag

from botcity.web import WebBot, Browser
# Uncomment the line below for integrations with BotMaestro
# Using the Maestro SDK
# from botcity.maestro import *

ENV_START_URL = "BOT_START_URL"
ENV_MAX_PAGES = "BOT_MAX_PAGES"
ENV_OUTPUT_CSV = "BOT_OUTPUT_CSV"
ENV_PAGE_TIMEOUT = "BOT_PAGE_TIMEOUT"

DEFAULT_PAGE_TIMEOUT = 30

CSV_FIELDNAMES = ["url", "title", "links", "status"]
STATUS_SUCCESS = "success"
STATUS_FAILED_PREFIX = "failed"


class ConfigError(Exception):
    """Raised when the crawl configuration read from the environment is invalid."""


@dataclass
class CrawlConfig:
    """Validated crawl configuration."""

    start_url: str
    max_pages: int
    output_csv: str
    page_timeout: int


def default_output_csv():
    """Return the default CSV path: `crawl_results.csv` in the project directory."""
    package_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(package_dir)
    return os.path.join(project_dir, "crawl_results.csv")


def load_config(env=None):
    """Read and validate the crawl configuration from the environment.

    Raises:
        ConfigError: if the start URL is missing or the page limit is not a
            positive integer.
    """
    env = os.environ if env is None else env

    start_url = (env.get(ENV_START_URL) or "").strip()
    if not start_url:
        raise ConfigError(
            "%s is not set. Please define it with the URL where the crawl "
            "should start." % ENV_START_URL
        )

    raw_max_pages = (env.get(ENV_MAX_PAGES) or "").strip()
    try:
        max_pages = int(raw_max_pages)
    except ValueError:
        raise ConfigError(
            "%s must be a positive integer, got %r."
            % (ENV_MAX_PAGES, raw_max_pages or "<not set>")
        )
    if max_pages <= 0:
        raise ConfigError(
            "%s must be a positive integer, got %d." % (ENV_MAX_PAGES, max_pages)
        )

    raw_timeout = (env.get(ENV_PAGE_TIMEOUT) or "").strip()
    if raw_timeout:
        try:
            page_timeout = int(raw_timeout)
        except ValueError:
            raise ConfigError(
                "%s must be a positive integer (seconds), got %r."
                % (ENV_PAGE_TIMEOUT, raw_timeout)
            )
        if page_timeout <= 0:
            raise ConfigError(
                "%s must be a positive integer (seconds), got %d."
                % (ENV_PAGE_TIMEOUT, page_timeout)
            )
    else:
        page_timeout = DEFAULT_PAGE_TIMEOUT

    output_csv = (env.get(ENV_OUTPUT_CSV) or "").strip() or default_output_csv()

    return CrawlConfig(
        start_url=start_url,
        max_pages=max_pages,
        output_csv=output_csv,
        page_timeout=page_timeout,
    )


class CSVRecorder:
    """Append-only CSV writer with resume support.

    Each processed page is flushed to disk immediately. URLs already present
    in the file are exposed via `done_urls` so reruns can skip them instead of
    writing duplicate rows.
    """

    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._done = set()
        self._recorded_links = []
        if os.path.exists(path):
            with open(path, "r", newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    url = (row.get("url") or "").strip()
                    if url:
                        self._done.add(url)
                    links = [
                        link.strip()
                        for link in (row.get("links") or "").split(";")
                        if link.strip()
                    ]
                    self._recorded_links.append(links)

        write_header = not os.path.exists(path) or os.path.getsize(path) == 0
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        if write_header:
            self._writer.writerow(CSV_FIELDNAMES)
            self._flush()

    @property
    def done_urls(self):
        """URLs already recorded in the CSV (successful or failed)."""
        return set(self._done)

    @property
    def recorded_links(self):
        """Links of every recorded page, in file order.

        Used to rebuild the pending crawl frontier after an interrupted run.
        """
        return [list(links) for links in self._recorded_links]

    def record(self, url, title, links, status):
        """Append one row for a processed page and flush it to disk."""
        self._writer.writerow([url, title, ";".join(links), status])
        self._flush()
        self._done.add(url)
        self._recorded_links.append(list(links))

    def _flush(self):
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def normalize_link(link):
    """Normalize a link for deduplication; return None for non-http(s) links."""
    link = (link or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        return None
    # Drop the fragment so /page#a and /page#b count as the same page.
    return urldefrag(link)[0]


class SiteCrawler:
    """Breadth-first crawl loop, independent from the browser implementation.

    The `loader` only needs to provide `open_page(url)`, `get_title()` and
    `get_links()`, which makes the crawl testable without a real browser.
    """

    def __init__(self, loader, recorder, start_url, max_pages):
        self._loader = loader
        self._recorder = recorder
        self._start_url = start_url
        self._max_pages = max_pages

    def run(self):
        """Crawl up to `max_pages` pages. Returns (success_count, failed_count)."""
        success = 0
        failed = 0
        processed = 0

        seen = self._recorder.done_urls
        queue = deque()
        start = normalize_link(self._start_url) or self._start_url
        if start not in seen:
            seen.add(start)
            queue.append(self._start_url)
        # Rebuild the pending frontier from links recorded by previous runs,
        # so pages discovered but not yet visited are not lost on restart.
        for links in self._recorder.recorded_links:
            for link in links:
                normalized = normalize_link(link)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    queue.append(normalized)

        while queue and processed < self._max_pages:
            url = queue.popleft()
            processed += 1
            try:
                self._loader.open_page(url)
                title = self._loader.get_title()
                links = self._loader.get_links()
            except Exception as exc:
                reason = "%s: %s" % (type(exc).__name__, exc)
                self._recorder.record(
                    url, "", [], "%s: %s" % (STATUS_FAILED_PREFIX, reason)
                )
                failed += 1
                continue

            self._recorder.record(url, title, links, STATUS_SUCCESS)
            success += 1

            for link in links:
                normalized = normalize_link(link)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    queue.append(normalized)

        return success, failed


class WebBotPageLoader:
    """Loads pages through the BotCity WebBot browser."""

    # JavaScript that collects the absolute URL of every anchor on the page.
    _LINKS_SCRIPT = (
        "return Array.prototype.slice.call("
        "document.querySelectorAll('a[href]'))"
        ".map(function (a) { return a.href; });"
    )

    def __init__(self, bot, page_timeout):
        self._bot = bot
        self._page_timeout = page_timeout
        self._timeout_applied = False

    def _ensure_driver(self):
        if self._bot.driver is None:
            self._bot.start_browser()
        if not self._timeout_applied:
            self._bot.driver.set_page_load_timeout(self._page_timeout)
            self._timeout_applied = True

    def open_page(self, url):
        self._ensure_driver()
        self._bot.browse(url)

    def get_title(self):
        return self._bot.page_title() or ""

    def get_links(self):
        links = self._bot.driver.execute_script(self._LINKS_SCRIPT)
        return [link for link in links if link]


class Bot(WebBot):
    def action(self, execution=None):
        # Uncomment to silence Maestro errors when disconnected
        # if self.maestro:
        #     self.maestro.RAISE_NOT_CONNECTED = False

        # Validate the configuration before starting the browser.
        try:
            config = load_config()
        except ConfigError as exc:
            print("Configuration error: %s" % exc, file=sys.stderr)
            raise SystemExit(2)

        # Configure whether or not to run on headless mode
        self.headless = True

        # Uncomment to change the default Browser to Firefox
        # self.browser = Browser.FIREFOX

        # Uncomment to set the WebDriver path
        # self.driver_path = "<path to your WebDriver binary>"

        # Fetch the Activity ID from the task:
        # task = self.maestro.get_task(execution.task_id)
        # activity_id = task.activity_id

        recorder = CSVRecorder(config.output_csv)
        loader = WebBotPageLoader(self, config.page_timeout)
        crawler = SiteCrawler(
            loader, recorder, config.start_url, config.max_pages
        )

        try:
            success, failed = crawler.run()
        finally:
            # Always clean up, even if the crawl is interrupted.
            recorder.close()
            self.stop_browser()

        print(
            "Crawl finished: %d page(s) succeeded, %d page(s) failed. "
            "Results written to %s" % (success, failed, config.output_csv)
        )

        # Uncomment to mark this task as finished on BotMaestro
        # self.maestro.finish_task(
        #     task_id=execution.task_id,
        #     status=AutomationTaskFinishStatus.SUCCESS,
        #     message="Task Finished OK.",
        #     total_items=0,
        #     processed_items=0,
        #     failed_items=0
        # )

    def not_found(self, label):
        print(f"Element not found: {label}")


if __name__ == '__main__':
    Bot.main()
