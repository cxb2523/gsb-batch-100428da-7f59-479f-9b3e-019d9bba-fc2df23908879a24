"""In-memory fake Selenium driver for offline tests.

Only the surface used by webscrapping.py is implemented. A "page" is a list of
job card dicts; opening a card switches the fake into a detail view that serves
the required element IDs. Faults can be injected per (page, card_index):

* ``page_load_faults``   -> driver.get raises
* ``title_faults``       -> driver.title lacks the expected fragment
* ``list_missing``       -> title element of the card is missing
* ``click_always``       -> card.click always raises
* ``click_recover``      -> first click raises, snackbar dismissal makes it work
* ``detail_missing``     -> a required detail element is missing
* ``salary_missing``     -> optional salary elements are missing (job still ok)
"""
from __future__ import annotations

import re

from selenium.common.exceptions import NoSuchElementException

PAGE_RE = re.compile(r'[?&]page=(\d+)')


class FakeElement:
    def __init__(self, text: str = '', on_click=None):
        self.text = text
        self._on_click = on_click

    def click(self):
        if self._on_click is not None:
            self._on_click()


class FakeDriver:
    def __init__(
        self,
        pages: list[list[dict]],
        *,
        page_load_faults: set[int] | None = None,
        title_faults: set[int] | None = None,
        list_missing: set[tuple[int, int]] | None = None,
        click_always: set[tuple[int, int]] | None = None,
        click_recover: set[tuple[int, int]] | None = None,
        detail_missing: set[tuple[int, int]] | None = None,
        salary_missing: set[tuple[int, int]] | None = None,
    ):
        self.pages = pages
        self.page_load_faults = page_load_faults or set()
        self.title_faults = title_faults or set()
        self.list_missing = list_missing or set()
        self.click_always = click_always or set()
        self.click_recover = click_recover or set()
        self.detail_missing = detail_missing or set()
        self.salary_missing = salary_missing or set()

        self.page: int | None = None
        self.view = 'list'
        self.detail_job: dict | None = None
        self.click_attempts: dict[tuple[int, int], int] = {}
        self.snackbar_dismissed = 0
        self.closed = False

    # -- navigation ---------------------------------------------------------
    def get(self, url: str) -> None:
        match = PAGE_RE.search(url)
        if match is None:
            raise ValueError(f'bad url: {url}')
        page = int(match.group(1))
        if page in self.page_load_faults:
            raise ConnectionError(f'simulated load failure on page {page}')
        self.page = page
        self.view = 'list'
        self.detail_job = None

    @property
    def title(self) -> str:
        if self.page in self.title_faults:
            return 'Some Other Site'
        return 'MyCareersFuture - jobs'

    def back(self) -> None:
        if self.view != 'detail':
            raise AssertionError('back() called outside detail view')
        self.view = 'list'
        self.detail_job = None

    def quit(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True

    # -- element lookup -----------------------------------------------------
    def find_element(self, by: str, value: str) -> FakeElement:
        if by == 'id':
            return self._find_by_id(value)
        if by == 'xpath':
            return self._find_by_xpath(value)
        raise NoSuchElementException(f'unsupported locator {by}={value!r}')

    def _find_by_id(self, element_id: str) -> FakeElement:
        id_map = {
            'employment_type': 'employment_type',
            'seniority': 'level',
            'job-categories': 'category',
            'job_description': 'description',
            'requirements': 'requirements',
        }
        if self.view != 'detail' or self.detail_job is None:
            raise NoSuchElementException(f'element id={element_id} not on list view')
        key = (self.page, self.detail_job['card_index'])
        if key in self.detail_missing and element_id in id_map:
            raise NoSuchElementException(
                f'simulated missing detail element {element_id}'
            )
        field = id_map.get(element_id)
        if field is None:
            raise NoSuchElementException(f'element id={element_id} not found')
        return FakeElement(self.detail_job[field])

    def _find_by_xpath(self, xpath: str) -> FakeElement:
        if xpath == "//div[@id='snackbar']/div/div/span":
            return FakeElement(on_click=self._dismiss_snackbar)
        if xpath.startswith("//div[@class='lh-solid']"):
            if self.view != 'detail':
                raise NoSuchElementException('salary element outside detail view')
            key = (self.page, self.detail_job['card_index'])
            if key in self.salary_missing:
                raise NoSuchElementException('simulated missing salary')
            if xpath.endswith('span[1]'):
                return FakeElement(self.detail_job['salary_min'])
            if xpath.endswith('span[2]'):
                return FakeElement(self.detail_job['salary_max'])
            raise NoSuchElementException(xpath)
        if xpath.startswith("//div[@class='salary tr-l']"):
            key = (self.page, self.detail_job['card_index'])
            if key in self.salary_missing:
                raise NoSuchElementException('simulated missing salary')
            return FakeElement(self.detail_job['salary_type'])

        card_match = re.search(r"card-list'\]/div\[(\d+)\]", xpath)
        if card_match is None:
            raise NoSuchElementException(f'unsupported xpath {xpath!r}')
        card_index = int(card_match.group(1))
        if self.view != 'list':
            raise NoSuchElementException('card xpath used outside list view')
        return self._list_element(card_index, xpath)

    def _list_element(self, card_index: int, xpath: str) -> FakeElement:
        key = (self.page, card_index)
        if key in self.list_missing:
            raise NoSuchElementException(
                f'simulated missing list element on {key}'
            )
        try:
            job = self.pages[self.page][card_index - 1]
        except IndexError as exc:
            raise NoSuchElementException(str(exc)) from exc

        if xpath.endswith('h1'):
            return FakeElement(job['title'])
        if xpath.endswith('p[1]'):
            return FakeElement(job['location'])

        if xpath == (
            "//div[@class='card-list']/div[{}]".format(card_index)
        ):
            return FakeElement(on_click=lambda: self._click_card(card_index, job))
        raise NoSuchElementException(xpath)

    # -- card interactions --------------------------------------------------
    def _click_card(self, card_index: int, job: dict) -> None:
        key = (self.page, card_index)
        self.click_attempts[key] = self.click_attempts.get(key, 0) + 1
        if key in self.click_always:
            raise ElementClickIntercepted('simulated permanent click failure')
        if key in self.click_recover:
            if self.click_attempts[key] == 1:
                raise ElementClickIntercepted('simulated transient click failure')
        self.view = 'detail'
        self.detail_job = dict(job)
        self.detail_job['card_index'] = card_index

    def _dismiss_snackbar(self) -> None:
        self.snackbar_dismissed += 1


class ElementClickIntercepted(Exception):
    """Stand-in for selenium's click-interception exception."""


def build_pages(num_pages: int, cards_per_page: int) -> list[list[dict]]:
    pages = []
    for page in range(num_pages):
        cards = []
        for card_index in range(1, cards_per_page + 1):
            n = page * cards_per_page + card_index
            cards.append(
                {
                    'title': f'Job {n}',
                    'location': f'Location {n}',
                    'employment_type': 'Full Time',
                    'level': 'Executive',
                    'category': 'Analytics',
                    'salary_min': f'$min{n}',
                    'salary_max': f'$max{n}',
                    'salary_type': 'Monthly',
                    'description': f'Description {n}',
                    'requirements': f'Requirements {n}',
                }
            )
        pages.append(cards)
    return pages
