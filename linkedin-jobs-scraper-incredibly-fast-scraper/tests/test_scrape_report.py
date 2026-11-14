import json
import os
import sys
from typing import Dict, List, Optional, Union
from urllib.parse import parse_qs, urlparse

import pytest
import requests

# Make src importable when running pytest from repo root
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(CURRENT_DIR, "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import main as cli  # type: ignore
from extractors import linkedin_parser  # type: ignore
from extractors.linkedin_parser import LinkedInJobScraper  # type: ignore

JOB_FIELDS = {
    "job_title",
    "company_name",
    "location",
    "date_posted",
    "job_description",
    "seniority_level",
    "employment_type",
    "industries",
}

SEARCH_URL = "https://jobs.test/linkedin/search?keywords=Engineer&location=Remote"


def make_jobs_html(titles: List[str]) -> str:
    """Build a LinkedIn-like results page holding one card per title."""
    cards = []
    for title in titles:
        cards.append(
            f"""
            <li class="jobs-search-results__list-item">
              <div class="base-card">
                <h3 class="base-search-card__title">{title}</h3>
                <a class="base-search-card__subtitle">Acme {title} Corp</a>
                <span class="job-search-card__location">Remote</span>
                <time datetime="2025-11-01">1 day ago</time>
                <span class="job-card-container__metadata-item--seniority">Mid-Level</span>
                <span class="job-card-container__metadata-item--employment-type">Full-Time</span>
              </div>
            </li>
            """
        )
    return "<html><body><ul class='jobs-search__results-list'>" + "".join(cards) + "</ul></body></html>"


def make_page(start: int, count: int) -> str:
    return make_jobs_html([f"Engineer {start + i}" for i in range(count)])


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    """Deterministic stand-in for requests.Session keyed by the `start` param."""

    def __init__(self, pages: Dict[int, Union[str, Exception]]) -> None:
        self.pages = pages
        self.calls: List[int] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        query = parse_qs(urlparse(url).query)
        start = int(query["start"][0])
        self.calls.append(start)
        page = self.pages[start]
        if isinstance(page, Exception):
            raise page
        return FakeResponse(page)


def build_scraper(
    pages: Dict[int, Union[str, Exception]],
    max_retries: int = 2,
    max_jobs: int = 1000,
) -> tuple[LinkedInJobScraper, FakeSession]:
    scraper = LinkedInJobScraper(
        proxy_manager=None,
        max_retries=max_retries,
        request_timeout=1,
        delay_range=(0.0, 0.0),
    )
    session = FakeSession(pages)
    scraper.session = session  # type: ignore[assignment]
    return scraper, session


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linkedin_parser.time, "sleep", lambda _seconds: None)


def test_all_pages_succeed_until_max_jobs_reached():
    pages = {
        0: make_page(0, 25),
        25: make_page(25, 25),
        50: make_page(50, 25),
    }
    scraper, session = build_scraper(pages)
    report = scraper.scrape_search_with_report(SEARCH_URL, max_jobs=30)

    assert session.calls == [0, 25]
    assert [page.start for page in report.pages] == [0, 25]
    assert [page.job_count for page in report.pages] == [25, 25]
    assert [page.outcome for page in report.pages] == [
        linkedin_parser.PAGE_OUTCOME_SUCCESS,
        linkedin_parser.PAGE_OUTCOME_MAX_JOBS_REACHED,
    ]
    assert report.stop_reason == linkedin_parser.STOP_REASON_MAX_JOBS_REACHED
    assert report.failed is False
    assert len(report.jobs) == 30
    assert report.pages[1].jobs_added == 5


def test_all_success_then_empty_page_stops_pagination():
    pages = {
        0: make_page(0, 25),
        25: make_jobs_html([]),
    }
    scraper, session = build_scraper(pages)
    report = scraper.scrape_search_with_report(SEARCH_URL, max_jobs=1000)

    assert session.calls == [0, 25]
    assert [page.start for page in report.pages] == [0, 25]
    assert [page.job_count for page in report.pages] == [25, 0]
    assert [page.outcome for page in report.pages] == [
        linkedin_parser.PAGE_OUTCOME_SUCCESS,
        linkedin_parser.PAGE_OUTCOME_EMPTY_PAGE,
    ]
    assert report.stop_reason == linkedin_parser.STOP_REASON_EMPTY_PAGE
    assert report.failed is False
    assert len(report.jobs) == 25


def test_second_page_failure_keeps_first_page_jobs():
    failure = requests.ConnectionError("connection reset by peer")
    pages = {
        0: make_page(0, 25),
        25: failure,
        50: make_page(50, 25),
    }
    scraper, session = build_scraper(pages, max_retries=2)
    report = scraper.scrape_search_with_report(SEARCH_URL, max_jobs=1000)

    # Retries exhausted on page 25, and no request is made for later pages.
    assert session.calls == [0, 25, 25]
    assert [page.start for page in report.pages] == [0, 25]
    assert [page.job_count for page in report.pages] == [25, 0]
    assert [page.outcome for page in report.pages] == [
        linkedin_parser.PAGE_OUTCOME_SUCCESS,
        linkedin_parser.PAGE_OUTCOME_REQUEST_FAILED,
    ]
    assert "ConnectionError" in (report.pages[1].error or "")
    assert report.stop_reason == linkedin_parser.STOP_REASON_REQUEST_FAILED
    assert report.failed is True
    assert len(report.jobs) == 25
    assert report.jobs[0]["job_title"] == "Engineer 0"


def test_scrape_search_keeps_raising_on_page_failure():
    pages = {
        0: make_page(0, 25),
        25: requests.Timeout("timed out"),
    }
    scraper, _ = build_scraper(pages, max_retries=2)

    with pytest.raises(requests.Timeout):
        scraper.scrape_search(SEARCH_URL, max_jobs=1000)


def write_settings(tmp_path: pytest.TempPathFactory) -> str:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "max_retries": 2,
                "request_timeout": 1,
                "delay_range": [0.0, 0.0],
                "proxies": [],
            }
        ),
        encoding="utf-8",
    )
    return str(settings_path)


def install_fake_session(monkeypatch: pytest.MonkeyPatch, pages) -> FakeSession:
    session = FakeSession(pages)

    def fake_session_factory() -> FakeSession:
        return session

    monkeypatch.setattr(linkedin_parser.requests, "Session", fake_session_factory)
    return session


def test_cli_exports_partial_jobs_and_default_report_on_failure(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    pages = {
        0: make_page(0, 3),
        25: requests.ConnectionError("boom"),
    }
    install_fake_session(monkeypatch, pages)

    output_path = tmp_path / "jobs.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--url",
            SEARCH_URL,
            "--config",
            write_settings(tmp_path),
            "--output-file",
            str(output_path),
        ],
    )

    # Returning normally is exit code 0: partial results are still exported.
    cli.main()

    assert output_path.exists()
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(exported) == 3
    assert set(exported[0].keys()) == JOB_FIELDS

    report_path = tmp_path / "jobs.json.report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [page["start"] for page in report["pages"]] == [0, 25]
    assert [page["job_count"] for page in report["pages"]] == [3, 0]
    assert [page["outcome"] for page in report["pages"]] == [
        "success",
        "request_failed",
    ]
    assert report["stop_reason"] == "request_failed"
    assert report["failed"] is True
    assert report["stopped_early_on_failure"] is True
    assert len(report["jobs"]) == 3
    # Report data is separate from the job records.
    assert all(set(job.keys()) == JOB_FIELDS for job in report["jobs"])


def test_cli_honours_explicit_report_file(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    pages = {0: make_page(0, 2), 25: make_jobs_html([])}
    install_fake_session(monkeypatch, pages)

    output_path = tmp_path / "out" / "jobs.json"
    report_path = tmp_path / "reports" / "custom-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--url",
            SEARCH_URL,
            "--config",
            write_settings(tmp_path),
            "--output-file",
            str(output_path),
            "--report-file",
            str(report_path),
        ],
    )

    cli.main()

    assert output_path.exists()
    assert report_path.exists()
    assert not (tmp_path / "out" / "jobs.json.report.json").exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [page["start"] for page in report["pages"]] == [0, 25]
    assert [page["outcome"] for page in report["pages"]] == ["success", "empty_page"]
    assert report["stop_reason"] == "empty_page"
    assert report["failed"] is False


def test_cli_does_not_export_when_nothing_scraped(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    pages = {0: make_jobs_html([])}
    install_fake_session(monkeypatch, pages)

    output_path = tmp_path / "jobs.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--url",
            SEARCH_URL,
            "--config",
            write_settings(tmp_path),
            "--output-file",
            str(output_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0
    assert not output_path.exists()

    report = json.loads(
        (tmp_path / "jobs.json.report.json").read_text(encoding="utf-8")
    )
    assert report["stop_reason"] == "empty_page"
    assert report["jobs"] == []
