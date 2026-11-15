from bose import BaseTask, LocalStorage

import traceback
from datetime import datetime

# Keys used in local_storage.json. Keep them in one place so every run
# (fresh run and resume) reads and writes exactly the same keys.
SUCCESS_KEY = "scraped_pages"
FAILURE_KEY = "scrape_failures"

# The two kinds of per-link failure that are recorded.
STAGE_OPEN_PAGE = "open_page"
STAGE_EXTRACT_DATA = "extract_data"

REASON_OPEN_PAGE = "Failed to open page"
REASON_EXTRACT_DATA = "Failed to extract data"


def _timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _valid_record(record):
    return (
        isinstance(record, dict)
        and isinstance(record.get("link"), str)
        and bool(record.get("link"))
    )


def _index_records(raw_records):
    """Turn a value read from local storage into a ``{link: record}`` map.

    Anything that is not a list of records with a non-empty string ``link`` is
    ignored. Duplicate links collapse to the last record, which keeps repeated
    runs from accumulating duplicate entries.
    """
    by_link = {}

    if not isinstance(raw_records, list):
        return by_link

    for record in raw_records:
        if _valid_record(record):
            by_link[record["link"]] = record

    return by_link


def _attempts(record):
    try:
        return int(record.get("attempts") or 0)
    except (TypeError, ValueError):
        return 0


class ScrapePagesTask(BaseTask):
    """Open every link from ``local_storage.json`` and extract page data.

    A failure on one link no longer aborts the whole run:

    * the remaining links are still visited;
    * every successfully scraped page is returned as part of the result;
    * the failed link, the failure reason, the original exception (type,
      message and traceback) and its position are persisted.

    Two keys are written to ``local_storage.json``:

    ``scraped_pages``
        Records of successfully scraped pages. Each record has ``link``,
        ``index`` (position in the ``links`` list), ``data`` (the value
        returned by :meth:`extract_page_data`), ``attempts`` and
        ``scraped_at``.

    ``scrape_failures``
        Records of links that failed. Each record has ``link``, ``index``
        (position in the ``links`` list), ``run_index`` (position in the list
        of links processed by the current run), ``stage`` (either
        ``open_page`` or ``extract_data``), ``reason``, ``error_type``,
        ``error_message``, ``traceback``, ``attempts``, ``first_failed_at``
        and ``last_attempt_at``.

    Both keys are merged by ``link`` rather than replaced wholesale, so
    repeated runs add to the same consistent record set instead of producing
    duplicates or overwriting earlier results. Both keys are written after
    every link, so an interrupted run keeps whatever it has already completed.

    When failure records are already present, a run resumes instead of
    starting over: failed links are retried first and already successful links
    are skipped. A successful retry is removed from ``scrape_failures`` and
    added to ``scraped_pages`` with its ``attempts`` count preserved. Links
    that were never attempted (e.g. after an interruption, or newly appended
    to the queue) are visited after the retries. Success records for links
    that are no longer in the current queue are retained in storage but are
    not returned, while failure records for such links are dropped because
    they cannot be retried against the current queue.
    """

    success_key = SUCCESS_KEY
    failure_key = FAILURE_KEY

    def get_storage(self):
        """Storage used for links/results/failures (overridable for tests)."""
        return LocalStorage

    @staticmethod
    def normalize_links(raw_links):
        if raw_links is None:
            return []

        if isinstance(raw_links, str):
            raw_links = [raw_links]

        if not isinstance(raw_links, list):
            return []

        return [link for link in raw_links if isinstance(link, str) and link]

    def get_data(self):
        links = self.normalize_links(self.get_storage().get_item("links", []))
        if not links:
            # No links means there is nothing to do. Returning an empty data
            # list makes launch_tasks skip the task cleanly instead of
            # opening a browser for an empty queue. ``run`` also accepts an
            # empty queue and returns an empty result for direct callers.
            return []
        return [links]

    def open_page(self, driver, link):
        """Navigate to a link. Override if your driver needs another method."""
        driver.get_by_current_page_referrer(link)

    def extract_page_data(self, driver, link):
        # Write Code here to extract required data from the page and return as dictionary
        pass

    def prepare_session(self, driver):
        driver.organic_get("https://target-website.com/auth/sign-up/")

    def _prepare_session(self, driver):
        if getattr(driver, "organic_get", None) is None:
            return

        try:
            self.prepare_session(driver)
        except Exception as error:
            # Session setup is not a per-link scrape. A failure here should
            # not abort the round; links that cannot be opened will be
            # recorded individually below.
            print(
                "Session setup failed "
                f"({type(error).__name__}: {error}); continuing with the link queue."
            )

    def _persist(self, storage, success_by_link, failure_by_link):
        # Sort by position in the ``links`` list so the stored data is stable
        # and easy to compare with the queue, no matter in which order the
        # links were scraped (resumes retry failed links first).
        def sort_key(record):
            index = record.get("index")
            return (index if isinstance(index, int) else float("inf"))

        storage.set_item(
            self.success_key,
            sorted(success_by_link.values(), key=sort_key),
        )
        storage.set_item(
            self.failure_key,
            sorted(failure_by_link.values(), key=sort_key),
        )

    @staticmethod
    def _failure_record(link, index, run_index, stage, reason, error,
                        attempts, first_failed_at=None):
        now = _timestamp()
        return {
            "link": link,
            "index": index,
            "run_index": run_index,
            "stage": stage,
            "reason": reason,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "attempts": attempts,
            "first_failed_at": first_failed_at or now,
            "last_attempt_at": now,
        }

    @staticmethod
    def _result(success_by_link, link_positions):
        # Return one item per link in the current queue, in queue order. The
        # result therefore contains every known success (including ones that
        # were persisted by an earlier run), and never duplicates a link.
        return [
            success_by_link[link].get("data")
            for link in link_positions
            if link in success_by_link
        ]

    def run(self, driver, scraped_links):
        links = self.normalize_links(scraped_links)
        if not links:
            return []

        storage = self.get_storage()
        success_by_link = _index_records(storage.get_item(self.success_key, []))
        failure_by_link = _index_records(storage.get_item(self.failure_key, []))

        link_positions = {}
        for index, link in enumerate(links):
            link_positions.setdefault(link, index)

        # Drop inconsistent or stale failure records: a link that is already a
        # success must not be retried, and a failure for a link that is no
        # longer in the queue has nothing to do with this run.
        for link in list(failure_by_link):
            if link in success_by_link or link not in link_positions:
                failure_by_link.pop(link)

        pending_links = [
            link for link in link_positions if link not in success_by_link
        ]

        # Resume: retry the links that failed last time first, then visit
        # links that have never been attempted (e.g. a run interrupted before
        # reaching them, or links added to the queue afterwards). Successful
        # links are never visited again.
        failed_links = [
            link for link in pending_links if link in failure_by_link
        ]
        never_attempted = [
            link for link in pending_links if link not in failure_by_link
        ]
        work_links = failed_links + never_attempted

        if not work_links:
            self._persist(storage, success_by_link, failure_by_link)
            return self._result(success_by_link, link_positions)

        self._prepare_session(driver)

        for run_index, link in enumerate(work_links):
            index = link_positions[link]
            previous_failure = failure_by_link.get(link)
            attempts = _attempts(previous_failure) + 1 if previous_failure else 1
            first_failed_at = (
                previous_failure.get("first_failed_at")
                if previous_failure else None
            )

            try:
                self.open_page(driver, link)
            except Exception as error:
                failure_by_link[link] = self._failure_record(
                    link, index, run_index,
                    STAGE_OPEN_PAGE, REASON_OPEN_PAGE,
                    error, attempts, first_failed_at,
                )
                self._persist(storage, success_by_link, failure_by_link)
                continue

            try:
                item = self.extract_page_data(driver, link)
            except Exception as error:
                failure_by_link[link] = self._failure_record(
                    link, index, run_index,
                    STAGE_EXTRACT_DATA, REASON_EXTRACT_DATA,
                    error, attempts, first_failed_at,
                )
                self._persist(storage, success_by_link, failure_by_link)
                continue

            success_by_link[link] = {
                "link": link,
                "index": index,
                "data": item,
                "attempts": attempts,
                "scraped_at": _timestamp(),
            }
            # A successful retry is no longer a failure.
            failure_by_link.pop(link, None)
            self._persist(storage, success_by_link, failure_by_link)

        return self._result(success_by_link, link_positions)
