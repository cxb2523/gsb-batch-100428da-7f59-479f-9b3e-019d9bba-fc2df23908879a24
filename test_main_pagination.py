"""Tests for outcome-driven pagination in main._process_search_page.

No browser, AI service, or database is involved: get_job_listings,
navigation, filtering and scoring are all stubbed. Verifies that:
- a missing results container stops pagination for the keyword/location
- a page with zero successful cards stops pagination
- a partial failure with successes still processes those jobs and
  continues to the next page
- the existing skip/filter path remains intact
"""

import pytest

from linkedin_bot import main
from linkedin_bot.config import SearchConfig
from linkedin_bot.job_search import (
    FAILED_CARD_ID_PREFIX,
    FailedCard,
    JobListing,
    PageExtractionResult,
    PageOutcome,
)


class FakePage:
    """Records goto calls; everything else is stubbed at module level."""

    def __init__(self) -> None:
        self.goto_urls: list[str] = []

    async def goto(self, url: str, wait_until: str = "", timeout: int = 0) -> None:
        self.goto_urls.append(url)


class FakeTracker:
    """In-memory stand-in for ApplicationTracker dedup behavior."""

    def __init__(self, already: set[str] | None = None) -> None:
        self.applied_job_ids: set[str] = set(already or set())
        self.records: list[dict] = []

    def already_applied(self, job_id: str) -> bool:
        return job_id in self.applied_job_ids

    async def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


class FakeAI:
    def __init__(self) -> None:
        self.bulk_calls: int = 0

    async def calculate_match_scores_bulk(self, items: list[dict]) -> list[int]:
        self.bulk_calls += 1
        return [90 for _ in items]


def _listing(job_id: str, *, easy_apply: bool = True, applied: bool = False) -> JobListing:
    return JobListing(
        job_id=job_id,
        title=f"Engineer {job_id}",
        company="Acme Corp",
        location="Remote",
        description="Build things",
        url=f"https://www.linkedin.com/jobs/view/{job_id}/",
        is_easy_apply=easy_apply,
        already_applied=applied,
    )


def _result(
    outcome: PageOutcome,
    listings: tuple[JobListing, ...] = (),
    *,
    expected: int | None = None,
    failed: tuple[FailedCard, ...] = (),
) -> PageExtractionResult:
    return PageExtractionResult(
        outcome=outcome,
        listings=listings,
        expected_card_count=expected if expected is not None else len(listings) + len(failed),
        success_count=len(listings),
        failure_count=len(failed),
        failed_cards=failed,
    )


@pytest.fixture(autouse=True)
def _no_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_delay(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(main, "human_delay", _instant_delay)


@pytest.fixture
def _stub_apply(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stub scoring/application so no navigation or form work happens."""
    applied: list[str] = []

    async def _fake_process_job_application(**kwargs: object) -> bool:
        listing = kwargs["listing"]
        applied.append(listing.job_id)
        return True

    monkeypatch.setattr(main, "process_job_application", _fake_process_job_application)
    return applied


def _config() -> SearchConfig:
    return SearchConfig(keywords=["engineer"], locations=[""], blacklist_titles=["intern"])


async def _async_return(value: object):
    return value


async def _call(page: FakePage, result: PageExtractionResult, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "get_job_listings", lambda p: _async_return(result))
    return await main._process_search_page(
        page=page,
        keyword="engineer",
        location="",
        page_num=0,
        search_config=_config(),
        ai=FakeAI(),
        tracker=FakeTracker(),
        default_answers={},
        total_applied=0,
    )


async def test_container_missing_stops_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    result = _result(PageOutcome.CONTAINER_MISSING)

    total_applied, browser_alive, has_more = await _call(page, result, monkeypatch)

    assert browser_alive is True
    assert has_more is False  # pagination stops for this keyword/location
    assert total_applied == 0
    assert len(page.goto_urls) == 1


async def test_zero_success_stops_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()
    failed = (
        FailedCard(
            position=0,
            identifier=f"{FAILED_CARD_ID_PREFIX}pos-0",
            error="stale element",
        ),
        FailedCard(
            position=1,
            identifier=f"{FAILED_CARD_ID_PREFIX}pos-1",
            error="stale element",
        ),
    )
    # Container rendered but every card failed -> zero usable successes
    result = _result(PageOutcome.PARTIAL_FAILURE, (), expected=2, failed=failed)

    total_applied, browser_alive, has_more = await _call(page, result, monkeypatch)

    assert browser_alive is True
    assert has_more is False  # zero success stops pagination
    assert total_applied == 0
    # Every lost card is reported with its page position and placeholder id
    assert [card.position for card in failed] == [0, 1]
    assert all(card.identifier.startswith(FAILED_CARD_ID_PREFIX) for card in failed)


async def test_partial_failure_processes_successes_and_continues(
    monkeypatch: pytest.MonkeyPatch, _stub_apply: list
) -> None:
    page = FakePage()
    failed = (
        FailedCard(
            position=1,
            identifier=f"{FAILED_CARD_ID_PREFIX}7777",
            error="click intercepted",
        ),
    )
    listings = (_listing("5001"), _listing("5002"))
    result = _result(PageOutcome.PARTIAL_FAILURE, listings, expected=3, failed=failed)

    total_applied, browser_alive, has_more = await _call(page, result, monkeypatch)

    assert browser_alive is True
    assert has_more is True  # partial failure still paginates normally
    # Both successful listings were scored and applied; nothing was lost
    assert sorted(_stub_apply) == ["5001", "5002"]
    assert total_applied == 2
    # The failed placeholder was never treated as a job
    assert f"{FAILED_CARD_ID_PREFIX}7777" not in _stub_apply


async def test_complete_page_continues_pagination(
    monkeypatch: pytest.MonkeyPatch, _stub_apply: list
) -> None:
    page = FakePage()
    listings = (_listing("6001"),)
    result = _result(PageOutcome.COMPLETE, listings)

    total_applied, browser_alive, has_more = await _call(page, result, monkeypatch)

    assert browser_alive is True
    assert has_more is True
    assert _stub_apply == ["6001"]
    assert total_applied == 1


async def test_existing_skip_logic_unchanged_on_partial_page(
    monkeypatch: pytest.MonkeyPatch, _stub_apply: list
) -> None:
    """Applied badge / non-Easy-Apply / blacklist / dedup still filter out jobs."""
    page = FakePage()
    failed = (
        FailedCard(position=3, identifier=f"{FAILED_CARD_ID_PREFIX}pos-3", error="boom"),
    )
    listings = (
        _listing("7001", applied=True),  # already-applied badge
        _listing("7002", easy_apply=False),  # not Easy Apply
        JobListing(
            job_id="7003",
            title="Intern Engineer",  # blacklisted title keyword
            company="Acme Corp",
            location="Remote",
            description="x",
            url="u",
            is_easy_apply=True,
            already_applied=False,
        ),
        _listing("7004"),  # caught by the tracker dedup set
    )
    tracker = FakeTracker(already={"7004"})
    result = _result(PageOutcome.PARTIAL_FAILURE, listings, expected=5, failed=failed)

    monkeypatch.setattr(main, "get_job_listings", lambda p: _async_return(result))
    total_applied, _browser_alive, has_more = await main._process_search_page(
        page=page,
        keyword="engineer",
        location="",
        page_num=0,
        search_config=_config(),
        ai=FakeAI(),
        tracker=tracker,
        default_answers={},
        total_applied=0,
    )

    assert has_more is True  # partial page still paginates
    assert _stub_apply == []  # every successful listing was filtered out
    assert total_applied == 0
    # Skipped jobs were recorded (existing skip-recording behavior)
    assert len(tracker.records) == 3


async def test_partial_failure_with_successes_but_all_filtered_still_paginates(
    monkeypatch: pytest.MonkeyPatch, _stub_apply: list
) -> None:
    """Successful cards that all get filtered out do not stop pagination."""
    page = FakePage()
    failed = (FailedCard(position=1, identifier=f"{FAILED_CARD_ID_PREFIX}8", error="e"),)
    listings = (_listing("8001", easy_apply=False),)  # filtered by existing rules
    result = _result(PageOutcome.PARTIAL_FAILURE, listings, expected=2, failed=failed)

    total_applied, _browser_alive, has_more = await _call(page, result, monkeypatch)

    assert has_more is True
    assert _stub_apply == []
    assert total_applied == 0


# ── Loop-level integration through the real _run_session pagination loop ──


async def _scripted_session(
    monkeypatch: pytest.MonkeyPatch, scripted_pages: list[tuple[int, bool, bool]]
) -> list[int]:
    """Drive _run_session with scripted (total_applied, browser_alive, has_more).

    Returns the zero-based page numbers the loop actually visited.
    """
    visited: list[int] = []
    pages = list(scripted_pages)

    async def _fake_process(
        page: object, keyword: object, location: object, page_num: int, *args: object
    ) -> tuple[int, bool, bool]:
        visited.append(page_num)
        return pages[page_num]

    async def _true(*args: object, **kwargs: object) -> bool:
        return True

    async def _noop(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(main, "login", _true)
    monkeypatch.setattr(main, "save_session", _noop)
    monkeypatch.setattr(main, "print_stats", _noop)
    monkeypatch.setattr(main, "_process_search_page", _fake_process)
    monkeypatch.setattr(main.settings, "max_pages_per_search", len(pages))
    monkeypatch.setattr(main.settings, "max_applications_per_session", 100)

    await main._run_session(
        page=FakePage(),
        context=object(),
        search_config=_config(),
        ai=FakeAI(),
        tracker=FakeTracker(),
        default_answers={},
    )
    return visited


async def test_loop_stops_after_zero_success_page(monkeypatch: pytest.MonkeyPatch) -> None:
    # Page 0: partial failure with successes -> continue.
    # Page 1: zero successes -> stop; page 2 must never be reached.
    pages = [
        (1, True, True),   # partial: 1 applied, keep paginating
        (1, True, False),  # zero success: has_more False
        (5, True, True),   # would apply more if reached
    ]
    visited = await _scripted_session(monkeypatch, pages)

    assert visited == [0, 1]


async def test_loop_continues_through_partial_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        (1, True, True),  # partial
        (2, True, True),  # partial
        (3, True, True),  # complete page (max_pages reached anyway)
    ]
    visited = await _scripted_session(monkeypatch, pages)

    assert visited == [0, 1, 2]


async def test_loop_stops_immediately_when_container_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        (0, True, False),  # container missing on the first page
        (9, True, True),
    ]
    visited = await _scripted_session(monkeypatch, pages)

    assert visited == [0]


async def test_loop_browser_dead_ends_session(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        (0, False, False),  # browser died
        (9, True, True),
    ]
    visited = await _scripted_session(monkeypatch, pages)

    assert visited == [0]
