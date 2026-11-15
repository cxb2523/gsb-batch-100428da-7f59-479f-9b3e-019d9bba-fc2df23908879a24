"""Tests for page-level extraction outcomes in job_search.

Runs fully offline with stub page/card objects — no browser required.
Covers:
- all cards extracted successfully (COMPLETE)
- some cards raising during extraction (PARTIAL_FAILURE, successes kept)
- results container never appearing (CONTAINER_MISSING)
"""

from __future__ import annotations

import pytest

from linkedin_bot import job_search
from linkedin_bot.job_search import (
    FAILED_CARD_ID_PREFIX,
    FailedCard,
    JobListing,
    PageExtractionResult,
    PageOutcome,
    get_job_listings,
)


class FakeElement:
    """Minimal stand-in for a Playwright ElementHandle."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    async def inner_text(self) -> str:
        return self._text


class FakeCard:
    """Stub job card.

    raise_on_click makes card.click() throw to simulate a dead/stale card;
    attributes let the placeholder-identifier helper produce a stable,
    distinguishable id for the failed card.
    """

    def __init__(
        self,
        *,
        job_id: str | None = None,
        raise_on_click: bool = False,
        entity_urn: str | None = None,
    ) -> None:
        self._attrs: dict[str, str] = {}
        if job_id is not None:
            self._attrs["data-occludable-job-id"] = job_id
        if entity_urn is not None:
            self._attrs["data-occludable-entity-urn"] = entity_urn
        self.raise_on_click = raise_on_click
        self.clicked = False
        self._page: FakePage | None = None
        self._navigate_id = job_id or (
            entity_urn.rsplit(":", 1)[-1] if entity_urn else None
        )

    def _bind(self, page: FakePage) -> FakeCard:
        self._page = page
        return self

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)

    async def query_selector(self, selector: str) -> FakeElement | None:
        # No "applied" badge on stub cards.
        return None

    async def click(self) -> None:
        self.clicked = True
        if self.raise_on_click:
            raise RuntimeError("stale element: card detached from DOM")
        if self._page is not None and self._navigate_id is not None:
            self._page.url = f"https://www.linkedin.com/jobs/view/{self._navigate_id}/"


class FakePage:
    """Stub Playwright Page wired to the selectors job_search actually uses."""

    def __init__(self, cards: list[FakeCard] | None, *, url: str = "") -> None:
        self._cards = [card._bind(self) for card in cards] if cards else cards
        self.url = url
        self.selector_calls: list[str] = []
        # When cards exist the page behaves as if the results container
        # rendered; with None it behaves like a page without any results.
        self.has_container = cards is not None

    async def wait_for_selector(self, selector: str, timeout: int = 0) -> None:
        self.selector_calls.append(selector)
        if not self.has_container:
            raise RuntimeError(f"Timeout {timeout}ms exceeded for {selector}")

    async def query_selector(self, selector: str) -> FakeElement | None:
        if self.has_container and selector in job_search._JOB_LIST_SELECTORS:
            return FakeElement("results")
        # Details-panel selectors used by _extract_single_card
        if self.has_container and selector in job_search._TITLE_SELECTORS:
            return FakeElement("Engineer")
        if self.has_container and selector in job_search._COMPANY_SELECTORS:
            return FakeElement("Acme Corp")
        if self.has_container and selector == (
            ".job-details-jobs-unified-top-card__primary-description-container"
        ):
            return FakeElement("Remote")
        if self.has_container and selector in job_search._DESCRIPTION_SELECTORS:
            return FakeElement("Do the things")
        if self.has_container and selector.startswith("button.jobs-apply-button"):
            return FakeElement("Easy Apply")
        return None

    async def query_selector_all(self, selector: str) -> list[FakeCard]:
        if self._cards and selector in job_search._JOB_CARD_SELECTORS:
            return list(self._cards)
        return []

    async def evaluate(self, script: object, arg: object = None) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_human_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize all real sleeps touched by the extraction flow."""

    async def _instant_delay(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(job_search, "human_delay", _instant_delay)
    monkeypatch.setattr(job_search, "scroll_element", _instant_delay)


def _make_listing(job_id: str) -> JobListing:
    return JobListing(
        job_id=job_id,
        title="Engineer",
        company="Acme Corp",
        location="Remote",
        description="Do the things",
        url=f"https://www.linkedin.com/jobs/view/{job_id}/",
        is_easy_apply=True,
        already_applied=False,
    )


async def test_all_cards_succeed_is_complete() -> None:
    cards = [FakeCard(job_id="1001"), FakeCard(job_id="1002"), FakeCard(job_id="1003")]
    page = FakePage(cards, url="https://www.linkedin.com/jobs/view/1001/")

    result = await get_job_listings(page)

    assert isinstance(result, PageExtractionResult)
    assert result.outcome is PageOutcome.COMPLETE
    assert result.expected_card_count == 3
    assert result.success_count == 3
    assert result.failure_count == 0
    assert result.failed_cards == ()
    assert result.has_success is True
    assert [listing.job_id for listing in result.listings] == ["1001", "1002", "1003"]
    # Every card was actually visited
    assert all(card.clicked for card in cards)


async def test_partial_card_failures_keep_successes_and_record_positions() -> None:
    good_one = FakeCard(job_id="2001")
    broken = FakeCard(job_id="2002", raise_on_click=True)
    good_two = FakeCard(entity_urn="urn:li:jobPosting:2003")
    page = FakePage([good_one, broken, good_two], url="https://www.linkedin.com/jobs/view/2001/")

    result = await get_job_listings(page)

    assert result.outcome is PageOutcome.PARTIAL_FAILURE
    assert result.expected_card_count == 3
    assert result.success_count == 2
    assert result.failure_count == 1
    assert result.has_success is True

    # Successful listings survive the failure and stay in page order
    assert [listing.job_id for listing in result.listings] == ["2001", "2003"]

    failed = result.failed_cards
    assert len(failed) == 1
    failed_card = failed[0]
    assert isinstance(failed_card, FailedCard)
    assert failed_card.position == 1
    assert failed_card.identifier == f"{FAILED_CARD_ID_PREFIX}2002"
    assert "stale element" in failed_card.error

    # The placeholder must never masquerade as a real job_id or listing
    assert all(failed_card.identifier != listing.job_id for listing in result.listings)
    assert all(not listing.job_id.startswith(FAILED_CARD_ID_PREFIX) for listing in result.listings)
    # The failure did not stop processing of later cards
    assert good_two.clicked is True


async def test_failed_card_without_attributes_uses_position_placeholder() -> None:
    broken = FakeCard(raise_on_click=True)
    page = FakePage([broken], url="https://www.linkedin.com/jobs/search/?currentJobId=9999")

    result = await get_job_listings(page)

    assert result.outcome is PageOutcome.PARTIAL_FAILURE
    assert result.success_count == 0
    assert result.failure_count == 1
    placeholder = result.failed_cards[0].identifier
    assert placeholder == f"{FAILED_CARD_ID_PREFIX}pos-0"
    # Real ids are numeric; the placeholder is obviously not one
    assert not placeholder.removeprefix(FAILED_CARD_ID_PREFIX).isdigit()


async def test_missing_results_container_returns_container_missing() -> None:
    page = FakePage(None)  # no container, no cards

    result = await get_job_listings(page)

    assert result.outcome is PageOutcome.CONTAINER_MISSING
    assert result.expected_card_count == 0
    assert result.success_count == 0
    assert result.failure_count == 0
    assert result.failed_cards == ()
    assert result.listings == ()
    assert result.has_success is False


async def test_all_cards_failing_is_partial_failure_with_zero_success() -> None:
    cards = [FakeCard(raise_on_click=True), FakeCard(raise_on_click=True)]
    page = FakePage(cards)

    result = await get_job_listings(page)

    # Container was present, so this is a partial failure (not a missing
    # container), but the page has zero usable successes.
    assert result.outcome is PageOutcome.PARTIAL_FAILURE
    assert result.expected_card_count == 2
    assert result.success_count == 0
    assert result.failure_count == 2
    assert [failed.position for failed in result.failed_cards] == [0, 1]
    assert result.listings == ()
    assert result.has_success is False


async def test_max_jobs_caps_expected_card_count() -> None:
    cards = [FakeCard(job_id=str(3000 + i)) for i in range(5)]
    page = FakePage(cards)

    result = await get_job_listings(page, max_jobs=2)

    assert result.expected_card_count == 2
    assert result.success_count == 2
    assert result.outcome is PageOutcome.COMPLETE
    assert [listing.job_id for listing in result.listings] == ["3000", "3001"]
