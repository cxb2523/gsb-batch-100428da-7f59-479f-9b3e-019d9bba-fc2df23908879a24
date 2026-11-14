"""Tests for outcome propagation through GoogleMapsCrawler.crawl().

The browser is replaced with a fake browser manager and, depending on the
test, either the extractor or the page is faked. No Google Maps network access
happens.
"""

from __future__ import annotations

from core.crawler import GoogleMapsCrawler
from core.exceptions import DetailPageLoadError
from core.models import (
    CompanyData,
    CrawlResults,
    CrawlerConfig,
    ResultOutcome,
    ResultStatus,
)

from conftest import FakeBrowserManager, FakeGoogleMapsPage

FOO_URL = "https://www.google.com/maps/place/Foo+Bar/@52.5,13.4,17z/data=!4m1!3m1"
BAR_URL = "https://www.google.com/maps/place/Bar+Baz/@52.6,13.5,17z/data=!4m2!3m2"
BROKEN_URL = "https://www.google.com/maps/place/Broken+Place/@52.7,13.6,17z"


def _outcome(position, status, url, name, company=None, reason=""):
    return ResultOutcome(
        position=position,
        status=status,
        url=url,
        name=name,
        reason=reason,
        company=company,
    )


def make_extractor_class(extraction: CrawlResults, captured: dict):
    """Build a fake MapsExtractor class that returns a scripted extraction."""

    class FakeExtractor:
        def __init__(self, page, selector_timeout):
            captured["page"] = page
            captured["selector_timeout"] = selector_timeout

        @property
        def outcomes(self):
            return extraction.outcomes

        def extract_all(self, track_reviews=True):
            captured["track_reviews"] = track_reviews
            return extraction

    return FakeExtractor


def make_crawler(monkeypatch, extraction=None, page=None, captured=None):
    """Create a started crawler wired to fakes.

    If ``extraction`` is given, the extractor is faked.  Otherwise the real
    MapsExtractor runs against ``page``.
    """
    config = CrawlerConfig(search_prompt="cafes berlin", output_format="json")
    crawler = GoogleMapsCrawler(config)
    fake_browser = FakeBrowserManager(page if page is not None else object())

    monkeypatch.setattr("core.crawler.BrowserManager", lambda cfg: fake_browser)
    monkeypatch.setattr(
        "core.crawler.OutputHandler",
        lambda: type("H", (), {"output": lambda self, *args, **kwargs: None})(),
    )

    if extraction is not None:
        captured = captured if captured is not None else {}
        monkeypatch.setattr(
            "core.crawler.MapsExtractor", make_extractor_class(extraction, captured)
        )
        crawler._test_captured = captured

    crawler.start()
    return crawler


# ---------------------------------------------------------------------------
# Scripted-extractor tests (crawler post-processing logic)
# ---------------------------------------------------------------------------


def test_crawl_returns_list_semantics_and_outcomes(monkeypatch):
    foo = CompanyData(name="Foo", website="https://foo.example", rating="4.5")
    bar = CompanyData(name="Bar", website="https://bar.example")
    extraction = CrawlResults(
        [foo, bar],
        [
            _outcome(0, ResultStatus.SUCCESS, FOO_URL, "Foo", company=foo),
            _outcome(1, ResultStatus.REVIEWS_TIMEOUT, BAR_URL, "Bar", company=bar),
        ],
    )
    crawler = make_crawler(monkeypatch, extraction=extraction)

    results = crawler.crawl()

    # Existing list meaning and order are preserved.
    assert isinstance(results, list)
    assert results == [foo, bar]
    assert [c.name for c in results] == ["Foo", "Bar"]
    assert crawler.get_results() == [foo, bar]
    assert crawler.outcomes == results.outcomes
    assert [o.position for o in results.outcomes] == [0, 1]
    assert results.outcomes[0].status is ResultStatus.SUCCESS
    assert results.outcomes[1].status is ResultStatus.REVIEWS_TIMEOUT
    assert crawler._test_captured["track_reviews"] is True


def test_crawl_records_no_valid_website_filtered_company(monkeypatch):
    foo = CompanyData(name="Foo", website="https://foo.example")
    no_site = CompanyData(name="No Site", website="N/A", phone="+49 1")
    extraction = CrawlResults(
        [foo, no_site],
        [
            _outcome(0, ResultStatus.SUCCESS, FOO_URL, "Foo", company=foo),
            _outcome(1, ResultStatus.SUCCESS, BAR_URL, "No Site", company=no_site),
        ],
    )
    crawler = make_crawler(monkeypatch, extraction=extraction)

    results = crawler.crawl()

    # Filtered companies are still filtered from the returned list...
    assert len(results) == 1
    assert [c.name for c in results] == ["Foo"]
    # ...but their position outcome says why they are missing.
    filtered = results.outcomes[1]
    assert filtered.position == 1
    assert filtered.status is ResultStatus.NO_VALID_WEBSITE
    assert filtered.name == "No Site"
    assert filtered.url == BAR_URL
    assert "website" in filtered.reason


def test_crawl_marks_company_removed_by_deduplication(monkeypatch):
    foo = CompanyData(name="Foo", website="https://foo.example")
    foo_twin = CompanyData(name="Foo", website="https://foo.example")
    extraction = CrawlResults(
        [foo, foo_twin],
        [
            _outcome(0, ResultStatus.SUCCESS, FOO_URL, "Foo", company=foo),
            _outcome(1, ResultStatus.SUCCESS, BAR_URL, "Foo", company=foo_twin),
        ],
    )
    crawler = make_crawler(monkeypatch, extraction=extraction)

    results = crawler.crawl()

    assert results == [foo]
    removed = results.outcomes[1]
    assert removed.status is ResultStatus.DUPLICATE_COMPANY
    assert removed.duplicate_of == 0
    assert removed.reason


def test_crawl_keeps_empty_result_outcomes(monkeypatch):
    extraction = CrawlResults(
        [],
        [
            _outcome(
                0,
                ResultStatus.DETAIL_PAGE_FAILED,
                BROKEN_URL,
                "Broken Place",
                reason="detail page failed",
            )
        ],
    )
    crawler = make_crawler(monkeypatch, extraction=extraction)

    results = crawler.crawl()

    assert list(results) == []
    assert results.outcomes[0].status is ResultStatus.DETAIL_PAGE_FAILED
    assert results.outcomes[0].url == BROKEN_URL
    assert crawler.outcomes == results.outcomes


def test_crawl_without_prompt_returns_empty_results_with_outcomes():
    crawler = GoogleMapsCrawler(CrawlerConfig(search_prompt=""))

    results = crawler.crawl()

    assert isinstance(results, CrawlResults)
    assert list(results) == []
    assert results.outcomes == []
    assert crawler.outcomes == []


# ---------------------------------------------------------------------------
# End-to-end tests through the real MapsExtractor against a fake page
# ---------------------------------------------------------------------------


def test_crawl_end_to_end_normal_path(monkeypatch):
    page = FakeGoogleMapsPage(
        [FOO_URL, BAR_URL],
        {FOO_URL: "ok", BAR_URL: "ok"},
        company_names={FOO_URL: "Foo Bar", BAR_URL: "Bar Baz"},
    )
    crawler = make_crawler(monkeypatch, page=page)

    results = crawler.crawl()

    assert [c.name for c in results] == ["Foo Bar", "Bar Baz"]
    assert page.navigated_to == [FOO_URL, BAR_URL]
    assert [o.position for o in results.outcomes] == [0, 1]
    assert all(o.status is ResultStatus.SUCCESS for o in results.outcomes)
    assert results.outcomes[0].name == "Foo Bar"
    assert results.outcomes[0].url == FOO_URL


def test_crawl_end_to_end_detail_failure_reviews_timeout_and_duplicate(monkeypatch):
    duplicated = FOO_URL + "?hl=de"
    page = FakeGoogleMapsPage(
        [FOO_URL, duplicated, BAR_URL, BROKEN_URL],
        {
            FOO_URL: "ok",
            BAR_URL: "reviews_timeout",
            BROKEN_URL: DetailPageLoadError(message="page down", url=BROKEN_URL),
        },
        company_names={FOO_URL: "Foo Bar", BAR_URL: "Bar Baz", BROKEN_URL: "Broken Place"},
    )
    crawler = make_crawler(monkeypatch, page=page)

    results = crawler.crawl()

    # Only the duplicate is skipped; every other position is fetched.
    assert page.navigated_to == [FOO_URL, BAR_URL, BROKEN_URL]
    # The broken position is excluded, the others are still returned and keep order.
    assert [c.name for c in results] == ["Foo Bar", "Bar Baz"]

    by_position = {o.position: o for o in results.outcomes}
    assert sorted(by_position) == [0, 1, 2, 3]
    assert by_position[0].status is ResultStatus.SUCCESS
    assert by_position[1].status is ResultStatus.DUPLICATE
    assert by_position[1].duplicate_of == 0
    assert by_position[1].url == duplicated
    assert by_position[2].status is ResultStatus.REVIEWS_TIMEOUT
    assert by_position[2].name == "Bar Baz"
    assert by_position[3].status is ResultStatus.DETAIL_PAGE_FAILED
    assert by_position[3].name == "Broken Place"
    assert "page down" in by_position[3].reason


def test_crawl_end_to_end_no_valid_website_position(monkeypatch):
    page = FakeGoogleMapsPage(
        [FOO_URL, BAR_URL],
        {FOO_URL: "ok", BAR_URL: "ok"},
        company_names={FOO_URL: "Foo Bar", BAR_URL: "Bar Baz"},
        website="N/A",
    )
    crawler = make_crawler(monkeypatch, page=page)

    results = crawler.crawl()

    assert list(results) == []
    assert [o.status for o in results.outcomes] == [
        ResultStatus.NO_VALID_WEBSITE,
        ResultStatus.NO_VALID_WEBSITE,
    ]
    # Positions and names remain available for locating the filtered entries.
    assert [o.url for o in results.outcomes] == [FOO_URL, BAR_URL]
    assert [o.name for o in results.outcomes] == ["Foo Bar", "Bar Baz"]
