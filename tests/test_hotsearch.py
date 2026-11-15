"""Tests for the shared Baidu hot-search pipeline.

Everything is local: a fake Selenium driver and a fake database connection
stand in for the browser and MySQL, so no network, credentials or Chrome are
required.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hotsearch  # noqa: E402

FIXED_NOW = "2026-01-01 00:00:00"


class FakeElement:
    def __init__(self, text):
        self.text = text


class FakeDriver:
    def __init__(self, title_texts, heat_texts, fail_at=None):
        self._title_texts = title_texts
        self._heat_texts = heat_texts
        self.fail_at = fail_at
        self.calls = []
        self.closed = False
        self.get_url = None

    def get(self, url):
        self.calls.append("get")
        self.get_url = url
        if self.fail_at == "page_load":
            raise RuntimeError("timed out loading page")

    def find_elements(self, by, value):
        self.calls.append(("find_elements", by, value))
        if self.fail_at == "parse":
            raise RuntimeError("xpath evaluation failed")
        if value == hotsearch.TITLE_XPATH:
            return [FakeElement(t) for t in self._title_texts]
        if value == hotsearch.HEAT_XPATH:
            return [FakeElement(t) for t in self._heat_texts]
        raise AssertionError(f"unexpected xpath: {value}")

    def quit(self):
        self.closed = True


def driver_factory(title_texts, heat_texts, fail_at=None):
    created = []

    def factory():
        if fail_at == "browser_start":
            raise RuntimeError("could not start browser")
        driver = FakeDriver(title_texts, heat_texts, fail_at=fail_at)
        created.append(driver)
        return driver

    factory.created = created
    return factory


class FakeCursor:
    def __init__(self, conn, fail_contents=()):
        self._conn = conn
        self._fail_contents = set(fail_contents)
        self.closed = False

    def execute(self, sql, params):
        self._conn.executed.append((sql, params))
        content = params[1]
        if content in self._fail_contents:
            raise RuntimeError(f"insert rejected for {content!r}")

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail_contents=()):
        self.executed = []
        self.committed = []
        self.rolled_back = 0
        self.closed = False
        self.cursor_obj = FakeCursor(self, fail_contents)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        # Only rows since the last rollback are durable; track per-commit
        # counts so tests can prove successful rows survive later failures.
        self.committed.append(len(self.executed) - self.rolled_back)
        self._commit_seq = len(self.committed)

    def rollback(self):
        self.rolled_back = len(self.executed)

    def close(self):
        self.closed = True


def collector(conn):
    return lambda: conn


def test_mismatched_lengths_keep_pairable_prefix_and_report_drops():
    factory = driver_factory(["t1", "t2", "t3"], ["h1", "h2"])

    result = hotsearch.collect_hotsearch(driver_factory=factory, now=lambda: FIXED_NOW)

    assert [(i.title, i.heat) for i in result.items] == [("t1", "h1"), ("t2", "h2")]
    assert result.title_count == 3
    assert result.heat_count == 2
    assert len(result.dropped) == 1
    assert result.dropped[0].reason == hotsearch.DROP_MISSING_HEAT
    assert result.dropped[0].title == "t3"
    assert result.failed_stage is None
    assert result.fetched == 3
    # Reconciliation: nothing vanishes silently.
    assert result.fetched == len(result.items) + len(result.dropped)


def test_more_heats_than_titles_reports_missing_titles():
    factory = driver_factory(["t1"], ["h1", "h2", "h3"])

    result = hotsearch.collect_hotsearch(driver_factory=factory, now=lambda: FIXED_NOW)

    assert [(i.title, i.heat) for i in result.items] == [("t1", "h1")]
    assert [d.reason for d in result.dropped] == [
        hotsearch.DROP_MISSING_TITLE,
        hotsearch.DROP_MISSING_TITLE,
    ]
    assert [d.heat for d in result.dropped] == ["h2", "h3"]
    assert result.fetched == 3


def test_empty_elements_are_dropped_with_reason():
    factory = driver_factory(["good", "", "good2"], ["10", "20", ""])

    result = hotsearch.collect_hotsearch(driver_factory=factory, now=lambda: FIXED_NOW)

    assert [i.title for i in result.items] == ["good"]
    reasons = {d.index: d.reason for d in result.dropped}
    assert reasons[1] == hotsearch.DROP_EMPTY_TITLE
    assert reasons[2] == hotsearch.DROP_EMPTY_HEAT
    assert result.fetched == 3
    assert result.fetched == len(result.items) + len(result.dropped)


def test_browser_start_failure_is_attributed_to_that_stage():
    factory = driver_factory([], [], fail_at="browser_start")

    result = hotsearch.collect_hotsearch(driver_factory=factory, now=lambda: FIXED_NOW)

    assert result.failed_stage == hotsearch.STAGE_BROWSER_START
    assert "browser" in result.error.lower()
    assert result.complete_failure


def test_page_load_failure_is_attributed_to_that_stage():
    factory = driver_factory([], [], fail_at="page_load")

    result = hotsearch.collect_hotsearch(driver_factory=factory, now=lambda: FIXED_NOW)

    assert result.failed_stage == hotsearch.STAGE_PAGE_LOAD
    assert factory.created and factory.created[0].closed


def test_parse_failure_is_attributed_to_that_stage():
    factory = driver_factory([], [], fail_at="parse")

    result = hotsearch.collect_hotsearch(driver_factory=factory, now=lambda: FIXED_NOW)

    assert result.failed_stage == hotsearch.STAGE_PARSE


def test_no_elements_is_parse_failure_not_silent_empty_success():
    factory = driver_factory([], [])

    result = hotsearch.collect_hotsearch(driver_factory=factory, now=lambda: FIXED_NOW)

    assert result.failed_stage == hotsearch.STAGE_PARSE
    assert "structure" in result.error
    assert result.items == []


def test_driver_is_closed_even_when_parsing_fails():
    factory = driver_factory([], [], fail_at="parse")

    hotsearch.collect_hotsearch(driver_factory=factory, now=lambda: FIXED_NOW)

    assert factory.created[0].closed


def test_storage_uses_existing_hotsearch_dt_content_format():
    factory = driver_factory(["标题A", "标题B"], ["100", "200"])
    conn = FakeConnection()
    lines = []

    result = hotsearch.run_hotsearch(
        driver_factory=factory,
        connect_factory=collector(conn),
        now=lambda: FIXED_NOW,
        summary_sink=lines.append,
    )

    assert result.stored == 2
    assert all(sql == hotsearch._INSERT_SQL for sql, _ in conn.executed)
    assert [params for _, params in conn.executed] == [
        (FIXED_NOW, "标题A 100"),
        (FIXED_NOW, "标题B 200"),
    ]
    assert conn.closed
    assert len(lines) == 1


def test_one_row_failure_does_not_abort_batch_and_keeps_committed_rows():
    factory = driver_factory(["a", "b", "c"], ["1", "2", "3"])
    conn = FakeConnection(fail_contents={"b 2"})

    result = hotsearch.run_hotsearch(
        driver_factory=factory,
        connect_factory=collector(conn),
        now=lambda: FIXED_NOW,
        summary_sink=lambda line: None,
    )

    assert result.stored == 2
    assert [f.item.title for f in result.store_failures] == ["b"]
    assert result.failed_stage == hotsearch.STAGE_STORE
    # Third row was attempted after the second one failed.
    assert len(conn.executed) == 3
    # Two commits happened (rows a and c each committed on their own).
    assert len(conn.committed) == 2
    # The failure did not zero out the successful rows.
    assert not result.complete_failure
    assert result.fetched == result.stored + len(result.dropped) + len(result.store_failures)


def test_store_connection_failure_is_store_stage_and_raises_on_complete_failure():
    factory = driver_factory(["a"], ["1"])

    def broken_connect():
        raise RuntimeError("cannot connect to MySQL")

    with pytest.raises(hotsearch.HotSearchCollectionError) as excinfo:
        hotsearch.update_hotsearch(
            driver_factory=factory,
            connect_factory=broken_connect,
            now=lambda: FIXED_NOW,
            summary_sink=lambda line: None,
        )

    result = excinfo.value.result
    assert result.failed_stage == hotsearch.STAGE_STORE
    assert result.stored == 0
    assert result.dropped == []
    # Un-attempted rows are still accounted for, so the summary reconciles.
    assert [f.item.title for f in result.store_failures] == ["a"]
    assert "not attempted" in result.store_failures[0].reason
    assert result.fetched == result.stored + result.discarded + len(result.store_failures)


def test_cursor_creation_failure_closes_connection():
    class BadCursorConnection:
        def __init__(self):
            self.closed = False

        def cursor(self):
            raise RuntimeError("cursor creation failed")

        def close(self):
            self.closed = True

    factory = driver_factory(["a"], ["1"])
    conn = BadCursorConnection()

    with pytest.raises(hotsearch.HotSearchCollectionError):
        hotsearch.update_hotsearch(
            driver_factory=factory,
            connect_factory=lambda: conn,
            now=lambda: FIXED_NOW,
            summary_sink=lambda line: None,
        )

    assert conn.closed


def test_complete_browser_failure_raises_and_prints_summary():
    lines = []
    factory = driver_factory([], [], fail_at="browser_start")

    with pytest.raises(hotsearch.HotSearchCollectionError):
        hotsearch.update_hotsearch(
            driver_factory=factory,
            connect_factory=collector(FakeConnection()),
            now=lambda: FIXED_NOW,
            summary_sink=lines.append,
        )

    assert len(lines) == 1
    assert "stage=browser_start" in lines[0]


def test_summary_line_is_reconcilable():
    factory = driver_factory(["t1", "t2", "t3"], ["h1", "h2"])
    conn = FakeConnection()
    lines = []

    result = hotsearch.run_hotsearch(
        driver_factory=factory,
        connect_factory=collector(conn),
        now=lambda: FIXED_NOW,
        summary_sink=lines.append,
    )

    line = lines[0]
    assert f"time={FIXED_NOW}" in line
    assert "fetched=3" in line
    assert "stored=2" in line
    assert "discarded=1" in line
    assert "stage=none" in line
    assert "titles=3" in line and "heats=2" in line
    assert len(lines) == 1
    assert result.stored == 2


def test_partial_store_failure_returns_normally():
    factory = driver_factory(["a", "b"], ["1", "2"])
    conn = FakeConnection(fail_contents={"a 1"})

    result = hotsearch.update_hotsearch(
        driver_factory=factory,
        connect_factory=collector(conn),
        now=lambda: FIXED_NOW,
        summary_sink=lambda line: None,
    )

    assert isinstance(result, hotsearch.HotSearchResult)
    assert result.stored == 1
    assert len(result.store_failures) == 1
    assert result.failed_stage == hotsearch.STAGE_STORE


def test_spider_and_main_delegate_to_the_shared_implementation():
    import spider
    import main

    calls = []

    def fake_update(**kwargs):
        calls.append(("update", kwargs))
        return "sentinel"

    original = hotsearch.update_hotsearch
    hotsearch.update_hotsearch = fake_update
    try:
        assert spider.update_hotsearch() == "sentinel"
        assert main.update_hotsearch() == "sentinel"
    finally:
        hotsearch.update_hotsearch = original

    assert len(calls) == 2


def test_legacy_get_baidu_hot_wrappers_use_shared_parser(monkeypatch):
    import spider
    import main

    fake_result = hotsearch.HotSearchResult(
        started_at=FIXED_NOW,
        items=[
            hotsearch.HotSearchItem(0, "标题", "100"),
            hotsearch.HotSearchItem(1, "标题2", "200"),
        ],
    )
    monkeypatch.setattr(hotsearch, "collect_hotsearch", lambda **kwargs: fake_result)

    assert spider.get_baidu_hot() == [("标题", "100"), ("标题2", "200")]
    assert main.get_baidu_hot() == [["标题", "100"], ["标题2", "200"]]


def test_summary_uses_real_clock_when_now_not_injected():
    factory = driver_factory(["a"], ["1"])

    result = hotsearch.run_hotsearch(
        driver_factory=factory,
        connect_factory=collector(FakeConnection()),
        summary_sink=lambda line: None,
    )

    # started_at is a real timestamp, and it is what gets written to ``dt``.
    assert len(result.started_at) == len(FIXED_NOW)
    assert result.stored == 1


FIXTURE_PAGE = Path(__file__).resolve().parent / "fixtures" / "hotsearch_sample.html"


class FixtureDriver:
    """Minimal driver that evaluates the real XPaths against a local page."""

    def __init__(self, page_path):
        from lxml import html as lxml_html

        self._tree = lxml_html.parse(str(page_path))
        self.get_url = None
        self.closed = False
        self.xpath_calls = []

    def get(self, url):
        self.get_url = url

    def find_elements(self, by, value):
        assert by == "xpath", f"unexpected locator strategy: {by}"
        self.xpath_calls.append(value)
        return [FakeElement(e.text_content().strip()) for e in self._tree.xpath(value)]

    def quit(self):
        self.closed = True


def test_real_xpaths_resolve_against_local_sample_page():
    """The selectors in hotsearch.py match the intended page structure."""
    driver = FixtureDriver(FIXTURE_PAGE)

    result = hotsearch.collect_hotsearch(
        driver_factory=lambda: driver, now=lambda: FIXED_NOW
    )

    assert driver.get_url == hotsearch.BAIDU_HOT_URL
    assert driver.xpath_calls == [hotsearch.TITLE_XPATH, hotsearch.HEAT_XPATH]
    assert driver.closed

    # Only titles/heats in the targeted nesting are picked up; the unrelated
    # sidebar/header sibling is ignored.
    assert result.title_count == 3
    assert result.heat_count == 3
    assert [(i.title, i.heat) for i in result.items] == [
        ("本地样例热搜一", "4,912,345")
    ]
    reasons = [d.reason for d in result.dropped]
    assert reasons == [hotsearch.DROP_EMPTY_TITLE, hotsearch.DROP_EMPTY_HEAT]
    assert result.failed_stage is None
    assert result.fetched == 3


def test_real_xpath_length_mismatch_keeps_prefix_and_reports_tail():
    """Trim the page to two heat elements and three titles."""
    from lxml import html as lxml_html

    tree = lxml_html.parse(str(FIXTURE_PAGE))
    for heat in tree.xpath(hotsearch.HEAT_XPATH)[2:]:
        heat.getparent().remove(heat)

    class TrimmedDriver(FixtureDriver):
        def __init__(self):
            super().__init__(FIXTURE_PAGE)
            self._tree = tree

    result = hotsearch.collect_hotsearch(
        driver_factory=TrimmedDriver, now=lambda: FIXED_NOW
    )

    assert result.title_count == 3
    assert result.heat_count == 2
    assert result.dropped[0].reason == hotsearch.DROP_EMPTY_TITLE
    assert result.dropped[-1].reason == hotsearch.DROP_MISSING_HEAT
    assert result.dropped[-1].title == "本地样例热搜三"
    assert result.failed_stage is None
