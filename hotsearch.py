"""Shared Baidu hot-search collection pipeline.

Both :mod:`spider` and :mod:`main` use this module, so there is exactly one
implementation of the scrape/parse/store logic.

The pipeline keeps the run auditable:

* :func:`collect_hotsearch` opens the page and turns the two XPath result
  lists into title/heat pairs.  Titles and heats are paired by position; when
  the two lists have different lengths the pairable prefix is kept and every
  unpaired element is recorded in ``result.dropped`` with a reason.
* :func:`store_hotsearch` writes ``hotsearch (dt, content)`` rows.  Each row
  is committed independently, so one bad row cannot discard the rest of the
  batch.
* :func:`run_hotsearch` runs both phases, prints one summary line, and returns
  a :class:`HotSearchResult`.
* :func:`update_hotsearch` is the legacy entry point.  It additionally raises
  :class:`HotSearchCollectionError` when a run stored nothing at all.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import pymysql
from dotenv import load_dotenv

load_dotenv()

BAIDU_HOT_URL = "https://top.baidu.com/board?tab=realtime&sa=fyb_realtime_31065"
TITLE_XPATH = '//*[@id="sanRoot"]/main/div[2]/div/div[2]/div/div[2]/a/div[1]'
HEAT_XPATH = '//*[@id="sanRoot"]/main/div[2]/div/div[2]/div/div[1]/div[2]'

# Failure stages reported in HotSearchResult.failed_stage.
STAGE_BROWSER_START = "browser_start"
STAGE_PAGE_LOAD = "page_load"
STAGE_PARSE = "parse"
STAGE_STORE = "store"

# Reasons attached to entries that never become an insertable pair.
DROP_EMPTY_TITLE = "empty title"
DROP_EMPTY_HEAT = "empty heat"
DROP_MISSING_HEAT = "missing heat (title has no matching heat element)"
DROP_MISSING_TITLE = "missing title (heat has no matching title element)"

_INSERT_SQL = "INSERT INTO hotsearch (dt, content) VALUES (%s, %s)"


@dataclass(frozen=True)
class HotSearchItem:
    """One title/heat pair that is ready to be written to the database."""

    index: int
    title: str
    heat: str

    @property
    def content(self) -> str:
        """Value stored in the existing ``hotsearch.content`` column."""
        return f"{self.title} {self.heat}"


@dataclass(frozen=True)
class DroppedItem:
    """A scraped element that could not be paired or was empty."""

    index: int
    reason: str
    title: Optional[str] = None
    heat: Optional[str] = None


@dataclass(frozen=True)
class StoreFailure:
    """A pair that parsed correctly but failed to be written."""

    item: HotSearchItem
    reason: str


@dataclass
class HotSearchResult:
    """Structured outcome of one collection run."""

    started_at: str
    title_count: int = 0
    heat_count: int = 0
    items: list[HotSearchItem] = field(default_factory=list)
    dropped: list[DroppedItem] = field(default_factory=list)
    store_failures: list[StoreFailure] = field(default_factory=list)
    stored: int = 0
    failed_stage: Optional[str] = None
    error: Optional[str] = None

    @property
    def fetched(self) -> int:
        """Number of elements considered, before pairing/validation.

        ``fetched == stored + len(dropped) + len(store_failures)`` holds for
        any run that reached the store phase or failed before parsing.  If
        parsing itself failed, ``fetched`` may be 0 with items left
        unaccounted for, which ``failed_stage=parse`` communicates.
        """
        return max(self.title_count, self.heat_count)

    @property
    def discarded(self) -> int:
        return len(self.dropped)

    @property
    def complete_failure(self) -> bool:
        """True when the run failed and no row was stored."""
        return self.failed_stage is not None and self.stored == 0

    @property
    def ok(self) -> bool:
        return self.failed_stage is None and not self.store_failures

    def summary_line(self) -> str:
        """One-line, machine-checkable summary of this run."""
        stage = self.failed_stage or "none"
        parts = [
            "[hotsearch]",
            f"time={self.started_at}",
            f"fetched={self.fetched}",
            f"stored={self.stored}",
            f"discarded={self.discarded}",
            f"store_failed={len(self.store_failures)}",
            f"stage={stage}",
        ]
        if self.title_count != self.heat_count:
            parts.append(f"titles={self.title_count}")
            parts.append(f"heats={self.heat_count}")
        if self.error:
            parts.append(f"error={_one_line(self.error)}")
        return " ".join(parts)


class HotSearchCollectionError(RuntimeError):
    """Raised when a run failed and stored no rows at all."""

    def __init__(self, result: HotSearchResult) -> None:
        self.result = result
        stage = result.failed_stage or "unknown"
        super().__init__(
            f"hot-search run failed at stage '{stage}' "
            f"(fetched={result.fetched}, stored={result.stored}, "
            f"discarded={result.discarded}): {result.error or 'unknown error'}"
        )


def get_conn():
    """Open the project database connection used by the store phase."""
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        db=os.getenv("MYSQL_DATABASE"),
        charset="utf8",
    )
    return conn, conn.cursor()


def close_conn(conn, cursor) -> None:
    if cursor:
        try:
            cursor.close()
        except Exception:
            pass
    if conn:
        try:
            conn.close()
        except Exception:
            pass


def _default_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _default_driver_factory():
    # Imported lazily so importing this module never starts a browser and so
    # tests can inject a fake driver instead.
    from selenium.webdriver import Chrome, ChromeOptions

    options = ChromeOptions()
    options.add_argument("--headless")  # hide the browser
    options.add_argument("--no-sandbox")  # for linux
    return Chrome(options=options)


def _one_line(text: str, limit: int = 240) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _format_error(exc: BaseException) -> str:
    message = _one_line(str(exc))
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _element_text(element) -> str:
    text = getattr(element, "text", "") or ""
    return str(text).strip()


def _close_driver(driver) -> None:
    for method_name in ("quit", "close"):
        method = getattr(driver, method_name, None)
        if callable(method):
            try:
                method()
                return
            except Exception:
                continue


def _parse_elements(result: HotSearchResult, title_elements, heat_elements) -> None:
    titles = [_element_text(element) for element in (title_elements or [])]
    heats = [_element_text(element) for element in (heat_elements or [])]
    result.title_count = len(titles)
    result.heat_count = len(heats)

    paired = min(len(titles), len(heats))
    for index in range(paired):
        title, heat = titles[index], heats[index]
        if not title and not heat:
            result.dropped.append(DroppedItem(index, f"{DROP_EMPTY_TITLE} and {DROP_EMPTY_HEAT}"))
        elif not title:
            result.dropped.append(DroppedItem(index, DROP_EMPTY_TITLE, heat=heat))
        elif not heat:
            result.dropped.append(DroppedItem(index, DROP_EMPTY_HEAT, title=title))
        else:
            result.items.append(HotSearchItem(index, title, heat))

    # Do not silently truncate a length mismatch: every element beyond the
    # shorter list is reported as dropped with an explicit reason.
    for index in range(paired, len(titles)):
        result.dropped.append(
            DroppedItem(index, DROP_MISSING_HEAT, title=titles[index] or None)
        )
    for index in range(paired, len(heats)):
        result.dropped.append(
            DroppedItem(index, DROP_MISSING_TITLE, heat=heats[index] or None)
        )

    if result.fetched == 0:
        result.failed_stage = STAGE_PARSE
        result.error = "no hot-search elements found; the page structure may have changed"
    elif not result.items:
        result.failed_stage = STAGE_PARSE
        result.error = "no pairable hot-search entries found after validation"


def collect_hotsearch(
    driver_factory: Optional[Callable[[], object]] = None,
    *,
    url: str = BAIDU_HOT_URL,
    now: Optional[Callable[[], str]] = None,
) -> HotSearchResult:
    """Open the hot-search page and return the structured parse result.

    ``driver_factory`` is injectable for tests; it must return an object with
    ``get(url)``, ``find_elements(by, value)`` and a ``quit()``/``close()``
    method.  No database work happens here.
    """
    result = HotSearchResult(started_at=(now or _default_now)())
    factory = driver_factory or _default_driver_factory
    driver = None
    stage = STAGE_BROWSER_START
    try:
        driver = factory()
        stage = STAGE_PAGE_LOAD
        driver.get(url)
        stage = STAGE_PARSE
        title_elements = driver.find_elements("xpath", TITLE_XPATH)
        heat_elements = driver.find_elements("xpath", HEAT_XPATH)
        _parse_elements(result, title_elements, heat_elements)
    except Exception as exc:
        result.failed_stage = stage
        result.error = _format_error(exc)
    finally:
        if driver is not None:
            _close_driver(driver)
    return result


def _open_connection(connect_factory: Optional[Callable]):
    created = (connect_factory or get_conn)()
    if isinstance(created, tuple):
        return created[0], created[1]
    conn = created
    try:
        return conn, conn.cursor()
    except Exception:
        # Do not leak the connection when the cursor cannot be created.
        close_conn(conn, None)
        raise


def store_hotsearch(
    result: HotSearchResult,
    connect_factory: Optional[Callable] = None,
) -> HotSearchResult:
    """Write ``result.items`` to the ``hotsearch`` table.

    Each row is committed on its own: a failure on one row is recorded in
    ``result.store_failures`` and the loop continues, so rows that were
    already written stay committed.  ``failed_stage`` is set to
    ``STAGE_STORE`` if the connection cannot be opened or any row fails.  If
    the connection cannot be opened, every item is recorded as an
    un-attempted store failure so the run stays reconcilable.
    """
    if not result.items:
        return result

    try:
        conn, cursor = _open_connection(connect_factory)
    except Exception as exc:
        reason = f"not attempted: database connection failed: {_format_error(exc)}"
        result.store_failures.extend(
            StoreFailure(item=item, reason=reason) for item in result.items
        )
        result.failed_stage = STAGE_STORE
        result.error = f"database connection failed: {_format_error(exc)}"
        return result

    for item in result.items:
        try:
            cursor.execute(_INSERT_SQL, (result.started_at, item.content))
            conn.commit()
            result.stored += 1
        except Exception as exc:
            result.store_failures.append(StoreFailure(item=item, reason=_format_error(exc)))
            try:
                conn.rollback()
            except Exception:
                pass

    close_conn(conn, cursor)

    if result.store_failures:
        result.failed_stage = STAGE_STORE
        result.error = (
            f"{len(result.store_failures)} of {len(result.items)} rows failed to store"
        )
    return result


def run_hotsearch(
    driver_factory: Optional[Callable[[], object]] = None,
    connect_factory: Optional[Callable] = None,
    *,
    url: str = BAIDU_HOT_URL,
    now: Optional[Callable[[], str]] = None,
    summary_sink: Optional[Callable[[str], None]] = None,
) -> HotSearchResult:
    """Collect, store, print one summary line, and return the result."""
    result = collect_hotsearch(driver_factory=driver_factory, url=url, now=now)
    if result.items and result.failed_stage is None:
        store_hotsearch(result, connect_factory=connect_factory)
    (summary_sink or print)(result.summary_line())
    return result


def update_hotsearch(
    driver_factory: Optional[Callable[[], object]] = None,
    connect_factory: Optional[Callable] = None,
    *,
    url: str = BAIDU_HOT_URL,
    now: Optional[Callable[[], str]] = None,
    summary_sink: Optional[Callable[[str], None]] = None,
) -> HotSearchResult:
    """Run the full pipeline and raise on a complete failure.

    A complete failure is a run that stored no rows and has a
    ``failed_stage``.  Partial success (some rows stored) returns normally;
    the caller can still inspect ``result.store_failures`` and
    ``result.failed_stage``.
    """
    result = run_hotsearch(
        driver_factory=driver_factory,
        connect_factory=connect_factory,
        url=url,
        now=now,
        summary_sink=summary_sink,
    )
    if result.complete_failure:
        raise HotSearchCollectionError(result)
    return result


if __name__ == "__main__":
    try:
        update_hotsearch()
    except HotSearchCollectionError as exc:
        raise SystemExit(f"[hotsearch] {exc}") from None
