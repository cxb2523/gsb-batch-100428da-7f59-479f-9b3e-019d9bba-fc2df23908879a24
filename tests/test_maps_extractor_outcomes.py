"""Tests for per-position outcome tracking in MapsExtractor.

These tests use fake pages and scripted extractors only; they never touch
Google Maps or the network.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.exceptions import DetailPageLoadError, ReviewsTimeoutError
from core.extractors.maps_extractor import MapsExtractor
from core.models import CrawlResults, ResultStatus

from conftest import FakePage

FOO_URL = "https://www.google.com/maps/place/Foo+Bar/@52.5,13.4,17z/data=!4m1!3m1"
BAR_URL = "https://www.google.com/maps/place/Bar+Baz/@52.6,13.5,17z/data=!4m2!3m2"


class ScriptedExtractor(MapsExtractor):
    """MapsExtractor with scripted links and per-link detail results."""

    def __init__(self, links, detail_results):
        super().__init__(FakePage())
        self._links = links
        self._detail_results = detail_results
        self.detail_calls: list[tuple[str, int]] = []

    def _collect_result_links(self) -> list[str]:
        return list(self._links)

    def _extract_company_details(self, url, index, track_reviews=True):
        self.detail_calls.append((url, index))
        result = self._detail_results[index]
        if isinstance(result, Exception):
            raise result
        if result == "reviews_timeout":
            return self._make_company(url), "Reviews did not load within the timeout"
        return self._make_company(url), None

    @staticmethod
    def _make_company(url):
        from core.models import CompanyData

        return CompanyData(name=url.rsplit("/", 1)[-1], website="https://example.com")


def test_extract_all_success_path_records_one_outcome_per_position():
    extractor = ScriptedExtractor([FOO_URL, BAR_URL], {0: "ok", 1: "ok"})

    results = extractor.extract_all(track_reviews=True)

    assert isinstance(results, CrawlResults)
    assert len(results) == 2
    assert isinstance(results, list)
    assert [outcome.position for outcome in results.outcomes] == [0, 1]
    assert all(outcome.status is ResultStatus.SUCCESS for outcome in results.outcomes)
    assert results.outcomes[0].url == FOO_URL
    assert results.outcomes[0].company is results[0]
    assert extractor.outcomes == results.outcomes


def test_detail_page_failure_is_recorded_and_crawl_continues():
    extractor = ScriptedExtractor(
        [FOO_URL, BAR_URL],
        {0: DetailPageLoadError(message="goto failed", url=FOO_URL), 1: "ok"},
    )

    results = extractor.extract_all()

    # The failing position does not abort the run, the next one is still done.
    assert extractor.detail_calls == [(FOO_URL, 0), (BAR_URL, 1)]
    assert len(results) == 1
    assert results[0].name == BAR_URL.rsplit("/", 1)[-1]

    failed = results.outcomes[0]
    assert failed.status is ResultStatus.DETAIL_PAGE_FAILED
    assert failed.position == 0
    assert failed.url == FOO_URL
    assert "goto failed" in failed.reason
    # The name is still usable for locating the position.
    assert failed.name == "Foo Bar"

    assert results.outcomes[1].status is ResultStatus.SUCCESS


def test_unexpected_exception_is_recorded_as_detail_page_failure():
    extractor = ScriptedExtractor([FOO_URL], {0: RuntimeError("boom")})

    results = extractor.extract_all()

    assert len(results) == 0
    assert results.outcomes[0].status is ResultStatus.DETAIL_PAGE_FAILED
    assert results.outcomes[0].url == FOO_URL
    assert "boom" in results.outcomes[0].reason


def test_duplicate_position_is_skipped_but_covered_by_successful_position():
    # Same place link, different tracking parameters.
    duplicated = FOO_URL + "?hl=de"
    extractor = ScriptedExtractor([FOO_URL, duplicated, BAR_URL], {0: "ok", 1: "ok", 2: "ok"})

    results = extractor.extract_all()

    # The duplicate was never fetched twice.
    assert [call[0] for call in extractor.detail_calls] == [FOO_URL, BAR_URL]
    assert len(results) == 2

    duplicate = results.outcomes[1]
    assert duplicate.status is ResultStatus.DUPLICATE
    assert duplicate.position == 1
    assert duplicate.duplicate_of == 0
    assert duplicate.url == duplicated
    assert duplicate.name == "Foo Bar"
    assert results.outcomes[0].status is ResultStatus.SUCCESS
    assert results.outcomes[2].status is ResultStatus.SUCCESS


def test_duplicate_after_failed_position_is_retried():
    # The first occurrence failed, so the later one is not "covered" and is
    # processed instead of being skipped.
    extractor = ScriptedExtractor(
        [FOO_URL, FOO_URL + "?hl=de"],
        {0: DetailPageLoadError(message="down", url=FOO_URL), 1: "ok"},
    )

    results = extractor.extract_all()

    assert [call[1] for call in extractor.detail_calls] == [0, 1]
    assert len(results) == 1
    assert results.outcomes[0].status is ResultStatus.DETAIL_PAGE_FAILED
    assert results.outcomes[1].status is ResultStatus.SUCCESS


def test_reviews_timeout_keeps_company_and_continues():
    extractor = ScriptedExtractor(
        [FOO_URL, BAR_URL],
        {0: "reviews_timeout", 1: "ok"},
    )

    results = extractor.extract_all(track_reviews=True)

    # Timeout does not drop the company or interrupt later positions.
    assert len(results) == 2
    assert results[0].reviews == []
    timeout = results.outcomes[0]
    assert timeout.status is ResultStatus.REVIEWS_TIMEOUT
    assert timeout.position == 0
    assert timeout.url == FOO_URL
    assert timeout.company is results[0]
    assert results.outcomes[1].status is ResultStatus.SUCCESS


def test_extract_company_details_raises_on_missing_detail_page():
    page = FakePage(name_selector_ok=False)
    extractor = MapsExtractor(page)

    with pytest.raises(DetailPageLoadError) as exc_info:
        extractor._extract_company_details(FOO_URL, 0)

    assert exc_info.value.url == FOO_URL
    assert isinstance(exc_info.value.cause, PlaywrightTimeoutError)


def test_extract_company_details_raises_on_navigation_error():
    page = FakePage(goto_error=RuntimeError("net::ERR_TIMED_OUT"))
    extractor = MapsExtractor(page)

    with pytest.raises(DetailPageLoadError) as exc_info:
        extractor._extract_company_details(FOO_URL, 0)

    assert "ERR_TIMED_OUT" in str(exc_info.value)
    assert exc_info.value.url == FOO_URL


def test_extract_company_details_reports_review_timeout_but_returns_company():
    page = FakePage(reviews_ok=False)
    extractor = MapsExtractor(page, reviews_timeout=10)

    company, warning = extractor._extract_company_details(FOO_URL, 0, track_reviews=True)

    assert company.name == "Test Company"
    assert company.reviews == []
    assert warning is not None and "timeout" in warning.lower()

    # And the raw reviews method signals the timeout explicitly.
    page2 = FakePage(reviews_ok=False)
    extractor2 = MapsExtractor(page2, reviews_timeout=10)
    with pytest.raises(ReviewsTimeoutError):
        extractor2._extract_reviews()


def test_extract_company_details_returns_full_data_on_normal_page():
    page = FakePage()
    extractor = MapsExtractor(page)

    company, warning = extractor._extract_company_details(FOO_URL, 0, track_reviews=False)

    assert warning is None
    assert company.name == "Test Company"
    assert company.rating == "4.5"
    assert company.website == "https://example.com"
    assert page.navigated_to == [FOO_URL]


def test_place_without_reviews_tab_is_not_a_timeout():
    page = FakePage(has_reviews_tab=False)
    extractor = MapsExtractor(page, reviews_timeout=10)

    company, warning = extractor._extract_company_details(FOO_URL, 0, track_reviews=True)

    # A place with no reviews at all is a normal success, not a timeout.
    assert warning is None
    assert company.reviews == []


def test_normalize_place_url_ignores_tracking_parameters():
    base = "https://www.google.com/maps/place/Foo+Bar/@52.5,13.4,17z/data=!4m1!3m1"
    variants = [
        base,
        base + "?entry=ttu",
        base + "#reviews",
        base.replace("https://www.", "https://"),
        base.replace("https://www.", "http://www.").rstrip("/"),
    ]

    normalized = {MapsExtractor._normalize_place_url(url) for url in variants}
    assert len(normalized) == 1


def test_empty_feed_returns_empty_results_with_no_outcomes():
    extractor = ScriptedExtractor([], {})

    results = extractor.extract_all()

    assert isinstance(results, CrawlResults)
    assert len(results) == 0
    assert results.outcomes == []
