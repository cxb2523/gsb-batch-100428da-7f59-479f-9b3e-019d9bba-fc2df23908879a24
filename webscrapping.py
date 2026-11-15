"""
MyCareersFuture analyst-job scraper.

The collection flow is intentionally failure-tolerant:

* a page that fails to load, or whose title assertion fails, is recorded as a
  failed page and collection continues with the next page instead of aborting;
* a job card that cannot be opened, or a detail page whose fields cannot be
  extracted, is skipped (page number + card index + failure stage are recorded)
  while every other card is still collected;
* ``analyst_job.csv`` is still written, together with a JSON outcome file
  (default ``scrape_outcome.json``) that records total pages, successful pages,
  the failed-page list, the number of stored jobs and the skipped-job list, so
  a truncated run can be told apart from a complete one afterwards.

The Chrome driver path, page limit and waiting times can be overridden through
constructor arguments, command line flags or ``MCF_*`` environment variables.
The defaults are identical to the values that were previously hard-coded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

# --- defaults (kept identical to the original hard-coded values) ------------
DEFAULT_DRIVER_PATH = '/Users/janice/Developer/chromedriver'
DEFAULT_MAX_PAGES = 64
DEFAULT_CARDS_PER_PAGE = 20
DEFAULT_PAGE_WAIT = 6.0
DEFAULT_DETAIL_WAIT = 8.0
DEFAULT_BACK_WAIT = 3.0
DEFAULT_SNACKBAR_WAIT = 2.0
DEFAULT_OUTPUT_CSV = 'analyst_job.csv'
DEFAULT_OUTCOME_FILE = 'scrape_outcome.json'
DEFAULT_SEARCH_KEYWORD = 'analyst'

SEARCH_URL_TEMPLATE = (
    'https://www.mycareersfuture.sg/search?search={keyword}'
    '&sortBy=new_posting_date&page={page}'
)
EXPECTED_TITLE_FRAGMENT = 'MyCareersFuture'
SNACKBAR_XPATH = "//div[@id='snackbar']/div/div/span"

COLUMNS = [
    'title',
    'location',
    'employment_type',
    'level',
    'category',
    'salary_min',
    'salary_max',
    'salary_type',
    'description',
    'requirements',
]


class JobScrapeError(Exception):
    """Raised when a single job card cannot be collected."""

    def __init__(self, page: int, card_index: int, stage: str, message: str):
        super().__init__(
            f'page={page} card_index={card_index} stage={stage}: {message}'
        )
        self.page = page
        self.card_index = card_index
        self.stage = stage
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {
            'page': self.page,
            'card_index': self.card_index,
            'stage': self.stage,
            'error': self.message,
        }


@dataclass
class ScrapeConfig:
    """Runtime configuration; build with :meth:`from_env` for env/CLI overrides."""

    driver_path: str = DEFAULT_DRIVER_PATH
    max_pages: int = DEFAULT_MAX_PAGES
    cards_per_page: int = DEFAULT_CARDS_PER_PAGE
    page_wait: float = DEFAULT_PAGE_WAIT
    detail_wait: float = DEFAULT_DETAIL_WAIT
    back_wait: float = DEFAULT_BACK_WAIT
    snackbar_wait: float = DEFAULT_SNACKBAR_WAIT
    output_csv: str = DEFAULT_OUTPUT_CSV
    outcome_file: str = DEFAULT_OUTCOME_FILE
    search_keyword: str = DEFAULT_SEARCH_KEYWORD
    # Injectable so tests can run without real waiting.
    sleep: Callable[[float], None] = field(default=time.sleep, compare=False)

    @classmethod
    def from_env(cls, **overrides: Any) -> 'ScrapeConfig':
        """Build a config from MCF_* environment variables.

        Non-None keyword arguments (e.g. parsed CLI flags) take precedence over
        the environment variables, which in turn take precedence over defaults.
        """
        values: dict[str, Any] = {
            'driver_path': os.getenv(
                'MCF_CHROMEDRIVER_PATH', DEFAULT_DRIVER_PATH
            ),
            'max_pages': int(os.getenv('MCF_MAX_PAGES', str(DEFAULT_MAX_PAGES))),
            'cards_per_page': int(
                os.getenv('MCF_CARDS_PER_PAGE', str(DEFAULT_CARDS_PER_PAGE))
            ),
            'page_wait': float(
                os.getenv('MCF_PAGE_WAIT', str(DEFAULT_PAGE_WAIT))
            ),
            'detail_wait': float(
                os.getenv('MCF_DETAIL_WAIT', str(DEFAULT_DETAIL_WAIT))
            ),
            'back_wait': float(
                os.getenv('MCF_BACK_WAIT', str(DEFAULT_BACK_WAIT))
            ),
            'snackbar_wait': float(
                os.getenv('MCF_SNACKBAR_WAIT', str(DEFAULT_SNACKBAR_WAIT))
            ),
            'output_csv': os.getenv('MCF_OUTPUT_CSV', DEFAULT_OUTPUT_CSV),
            'outcome_file': os.getenv(
                'MCF_OUTCOME_FILE', DEFAULT_OUTCOME_FILE
            ),
            'search_keyword': os.getenv(
                'MCF_SEARCH_KEYWORD', DEFAULT_SEARCH_KEYWORD
            ),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)


@dataclass
class ScrapeOutcome:
    """Verifiable summary of one collection run."""

    total_pages: int
    successful_pages: int
    failed_pages: list[dict[str, Any]]
    success_count: int
    skipped_jobs: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_driver(config: ScrapeConfig) -> webdriver.Chrome:
    """Start Chrome, failing loudly when the driver is missing or broken."""
    driver_path = config.driver_path
    if not driver_path or not os.path.exists(driver_path):
        raise FileNotFoundError(
            f"Chrome driver not found at '{driver_path}'. Pass --driver-path or "
            'set the MCF_CHROMEDRIVER_PATH environment variable to a valid '
            'chromedriver executable.'
        )
    try:
        return webdriver.Chrome(service=Service(executable_path=driver_path))
    except WebDriverException as exc:
        raise RuntimeError(
            f"Failed to start Chrome with driver '{driver_path}': {exc}. "
            'Check that the driver version matches the installed Chrome.'
        ) from exc


def _title_xpath(card_index: int) -> str:
    return (
        "//div[@class='card-list']/div[{}]/div/a/div[1]/div/section/"
        'div[2]/div/h1'
    ).format(card_index)


def _location_xpath(card_index: int) -> str:
    return (
        "//div[@class='card-list']/div[{}]/div/a/div[1]/div/section/"
        'div[2]/div[2]/section/p[1]'
    ).format(card_index)


def _card_xpath(card_index: int) -> str:
    return "//div[@class='card-list']/div[{}]".format(card_index)


def _scrape_one_job(
    driver: Any, config: ScrapeConfig, page: int, card_index: int
) -> dict[str, Any]:
    """Collect one job card; raise :class:`JobScrapeError` on any failure."""
    stage = 'list_extract'
    try:
        title = driver.find_element(By.XPATH, _title_xpath(card_index)).text
        location = driver.find_element(
            By.XPATH, _location_xpath(card_index)
        ).text
        card = driver.find_element(By.XPATH, _card_xpath(card_index))

        stage = 'click'
        try:
            card.click()
        except Exception:
            # A snackbar overlay sometimes intercepts the first click; dismiss
            # it and retry once, mirroring the original script.
            try:
                driver.find_element(By.XPATH, SNACKBAR_XPATH).click()
                config.sleep(config.snackbar_wait)
            except Exception:
                pass
            card.click()
        entered_detail = True

        stage = 'detail_extract'
        try:
            config.sleep(config.detail_wait)
            employment_type = driver.find_element(
                By.ID, 'employment_type'
            ).text
            level = driver.find_element(By.ID, 'seniority').text
            category = driver.find_element(By.ID, 'job-categories').text
            description = driver.find_element(By.ID, 'job_description').text
            requirements = driver.find_element(By.ID, 'requirements').text

            # Salary is optional on the portal; its absence must not skip the job.
            try:
                salary_min = driver.find_element(
                    By.XPATH, "//div[@class='lh-solid']/span[1]"
                ).text
                salary_max = driver.find_element(
                    By.XPATH, "//div[@class='lh-solid']/span[2]"
                ).text
                salary_type = driver.find_element(
                    By.XPATH, "//div[@class='salary tr-l']/span[3]"
                ).text
            except Exception:
                salary_min = salary_max = salary_type = None

            return {
                'title': title,
                'location': location,
                'employment_type': employment_type,
                'level': level,
                'category': category,
                'salary_min': salary_min,
                'salary_max': salary_max,
                'salary_type': salary_type,
                'description': description,
                'requirements': requirements,
            }
        finally:
            # Best effort: return to the card list so the next card can be read,
            # even if detail extraction above failed and this job is skipped.
            if entered_detail:
                try:
                    driver.back()
                    config.sleep(config.back_wait)
                except Exception:
                    pass
    except JobScrapeError:
        raise
    except Exception as exc:
        raise JobScrapeError(
            page, card_index, stage, f'{type(exc).__name__}: {exc}'
        ) from exc


def scrape_jobs(
    driver: Any, config: ScrapeConfig
) -> tuple[list[dict[str, Any]], ScrapeOutcome]:
    """Walk every configured page, isolating page- and card-level failures."""
    rows: list[dict[str, Any]] = []
    failed_pages: list[dict[str, Any]] = []
    skipped_jobs: list[dict[str, Any]] = []
    successful_pages = 0

    for page in range(config.max_pages):
        url = SEARCH_URL_TEMPLATE.format(
            keyword=config.search_keyword, page=page
        )
        try:
            driver.get(url)
            if EXPECTED_TITLE_FRAGMENT not in driver.title:
                raise AssertionError(
                    f'title {driver.title!r} does not contain '
                    f'{EXPECTED_TITLE_FRAGMENT!r}'
                )
            config.sleep(config.page_wait)
        except Exception as exc:
            # Record the failed page and keep going with the next one.
            failed_pages.append(
                {'page': page, 'reason': f'{type(exc).__name__}: {exc}'}
            )
            continue

        successful_pages += 1
        for card_index in range(1, config.cards_per_page + 1):
            try:
                rows.append(
                    _scrape_one_job(driver, config, page, card_index)
                )
            except JobScrapeError as exc:
                # One bad card must not affect the remaining cards or pages.
                skipped_jobs.append(exc.as_dict())
                continue

    outcome = ScrapeOutcome(
        total_pages=config.max_pages,
        successful_pages=successful_pages,
        failed_pages=failed_pages,
        success_count=len(rows),
        skipped_jobs=skipped_jobs,
    )
    return rows, outcome


def run_scrape(
    config: ScrapeConfig, driver: Any = None
) -> ScrapeOutcome:
    """Run a full collection and write the CSV plus the outcome file.

    A pre-built driver may be injected (used by tests with a fake driver);
    otherwise one is created via :func:`create_driver` and closed here.
    """
    owns_driver = driver is None
    if owns_driver:
        driver = create_driver(config)
    try:
        rows, outcome = scrape_jobs(driver, config)
    finally:
        if owns_driver:
            try:
                driver.quit()
            except Exception:
                pass

    pd.DataFrame(rows, columns=COLUMNS).to_csv(config.output_csv, index=False)
    with open(config.outcome_file, 'w', encoding='utf-8') as handle:
        json.dump(outcome.to_dict(), handle, ensure_ascii=False, indent=2)
    return outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Scrape analyst jobs from MyCareersFuture.'
    )
    parser.add_argument(
        '--driver-path',
        help='Path to chromedriver (env MCF_CHROMEDRIVER_PATH; '
        f'default {DEFAULT_DRIVER_PATH}).',
    )
    parser.add_argument(
        '--max-pages', type=int, help='Number of search pages (env MCF_MAX_PAGES).'
    )
    parser.add_argument(
        '--cards-per-page',
        type=int,
        help='Job cards expected per page (env MCF_CARDS_PER_PAGE).',
    )
    parser.add_argument('--page-wait', type=float, help='Seconds after page load.')
    parser.add_argument(
        '--detail-wait', type=float, help='Seconds after opening a job card.'
    )
    parser.add_argument(
        '--back-wait', type=float, help='Seconds after returning to the list.'
    )
    parser.add_argument(
        '--snackbar-wait',
        type=float,
        help='Seconds after dismissing the snackbar overlay.',
    )
    parser.add_argument('--output', help='Output CSV path (env MCF_OUTPUT_CSV).')
    parser.add_argument(
        '--outcome-file', help='Outcome JSON path (env MCF_OUTCOME_FILE).'
    )
    parser.add_argument(
        '--search-keyword', help='Search keyword (env MCF_SEARCH_KEYWORD).'
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = ScrapeConfig.from_env(
        driver_path=args.driver_path,
        max_pages=args.max_pages,
        cards_per_page=args.cards_per_page,
        page_wait=args.page_wait,
        detail_wait=args.detail_wait,
        back_wait=args.back_wait,
        snackbar_wait=args.snackbar_wait,
        output_csv=args.output,
        outcome_file=args.outcome_file,
        search_keyword=args.search_keyword,
    )
    try:
        outcome = run_scrape(config)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1

    print(
        f'pages {outcome.successful_pages}/{outcome.total_pages} ok '
        f'({len(outcome.failed_pages)} failed); '
        f'jobs stored: {outcome.success_count}; '
        f'jobs skipped: {len(outcome.skipped_jobs)}'
    )
    print(f'csv -> {config.output_csv}')
    print(f'outcome -> {config.outcome_file}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
