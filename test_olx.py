#!/usr/bin/env python3

import unittest
from contextlib import contextmanager
from typing import Dict, List, Set
from unittest import mock

import inject
from click.testing import CliRunner
from selenium.common.exceptions import NoSuchElementException, WebDriverException

import olx
from olx import (
    OUTCOME_END,
    OUTCOME_FAILED,
    OUTCOME_OK,
    OUTCOME_SKIPPED,
    REASON_EXCLUDED,
    REASON_LOAD_FAILED,
    REASON_NO_ADS,
    REASON_PAGINATION_FAILED,
    REASON_PAGINATION_LOOP,
    PageOutcome,
    format_page_outcome,
    parse_ads,
)

FIELDNAMES = {"link", "kind", "title", "price", "location"}

# Sentinel for a next-page element whose href cannot be read.
BROKEN_NEXT = "BROKEN"


class FakeElement:
    def __init__(self, href=None, text=""):
        self._href = href
        self.text = text

    def get_attribute(self, name):
        if self._href is None:
            raise WebDriverException("cannot read attribute")
        return self._href

    def find_element(self, by, value):
        raise NoSuchElementException("not found")


class FakeCard:
    def __init__(self, link):
        self._link = link

    def find_element(self, by, value):
        if (by, value) == olx.AD_LINK:
            return FakeElement(href=self._link)
        raise NoSuchElementException("not found")


class FakeDriver:
    """A minimal fake WebDriver driven by a dict of fake pages.

    Each page is a dict with optional keys:
      free     - list of ad links for free ad cards
      paid     - list of ad links for paid ad cards
      next     - href of the next page, None for no next page,
                 or BROKEN_NEXT for an unparseable next-page link
      fail     - driver.get raises WebDriverException
      no_cards - ad card lookup finds nothing (wait times out)
    """

    def __init__(self, pages: Dict[str, dict]):
        self.pages = pages
        self.current_url = None
        self.requested: List[str] = []

    def get(self, url):
        self.requested.append(url)
        page = self.pages[url]
        if page.get("fail"):
            raise WebDriverException("cannot load page")
        self.current_url = url

    @property
    def page(self):
        return self.pages[self.current_url]

    def find_elements(self, by, value):
        page = self.page
        if page.get("no_cards"):
            return []
        if (by, value) == olx.FREE_ADS:
            return [FakeCard(link) for link in page.get("free", [])]
        if (by, value) == olx.PAID_ADS:
            return [FakeCard(link) for link in page.get("paid", [])]
        return []

    def find_element(self, by, value):
        if (by, value) == olx.NEXT_ADS_PAGE:
            next_page = self.page.get("next")
            if next_page == BROKEN_NEXT:
                return FakeElement(href=None)
            if next_page is not None:
                return FakeElement(href=next_page)
        raise NoSuchElementException("not found")


class ParseAdsTestCase(unittest.TestCase):
    def setUp(self):
        inject.clear_and_configure(
            lambda binder: binder.bind(Set[str], set(FIELDNAMES))
        )

    def collect(self, pages, start, **kwargs):
        driver = FakeDriver(pages)
        outcomes: List[PageOutcome] = []
        ads = list(
            parse_ads(
                start,
                driver=driver,
                outcomes=outcomes,
                timeout=0.05,
                next_page_timeout=0.05,
                **kwargs,
            )
        )
        return driver, ads, outcomes

    @staticmethod
    def links_of(ads):
        return [ad["link"] for ad in ads]

    def test_normal_pagination_ends_with_end_outcome(self):
        pages = {
            "http://olx/1": {
                "free": ["http://ad/1", "http://ad/2"],
                "paid": ["http://ad/3"],
                "next": "http://olx/2",
            },
            "http://olx/2": {"free": ["http://ad/4"], "next": "http://olx/3"},
            "http://olx/3": {"free": ["http://ad/5"], "next": None},
        }

        driver, ads, outcomes = self.collect(pages, "http://olx/1")

        self.assertEqual(
            self.links_of(ads),
            ["http://ad/1", "http://ad/2", "http://ad/3", "http://ad/4", "http://ad/5"],
        )
        self.assertEqual(
            outcomes,
            [
                PageOutcome(1, OUTCOME_OK),
                PageOutcome(2, OUTCOME_OK),
                PageOutcome(3, OUTCOME_END),
            ],
        )
        self.assertEqual(
            driver.requested, ["http://olx/1", "http://olx/2", "http://olx/3"]
        )

    def test_single_page_without_next_is_normal_end(self):
        pages = {"http://olx/1": {"free": ["http://ad/1"], "next": None}}

        _, ads, outcomes = self.collect(pages, "http://olx/1")

        self.assertEqual(self.links_of(ads), ["http://ad/1"])
        self.assertEqual(outcomes, [PageOutcome(1, OUTCOME_END)])

    def test_page_load_failure_keeps_previous_ads(self):
        pages = {
            "http://olx/1": {"free": ["http://ad/1"], "next": "http://olx/2"},
            "http://olx/2": {"fail": True},
        }

        _, ads, outcomes = self.collect(pages, "http://olx/1")

        self.assertEqual(self.links_of(ads), ["http://ad/1"])
        self.assertEqual(
            outcomes,
            [
                PageOutcome(1, OUTCOME_OK),
                PageOutcome(2, OUTCOME_FAILED, REASON_LOAD_FAILED),
            ],
        )

    def test_missing_ad_cards_keeps_previous_ads(self):
        pages = {
            "http://olx/1": {"free": ["http://ad/1"], "next": "http://olx/2"},
            "http://olx/2": {"no_cards": True, "next": None},
        }

        _, ads, outcomes = self.collect(pages, "http://olx/1")

        self.assertEqual(self.links_of(ads), ["http://ad/1"])
        self.assertEqual(
            outcomes,
            [
                PageOutcome(1, OUTCOME_OK),
                PageOutcome(2, OUTCOME_FAILED, REASON_NO_ADS),
            ],
        )

    def test_pagination_loop_on_repeated_link_stops(self):
        pages = {
            "http://olx/1": {"free": ["http://ad/1"], "next": "http://olx/2"},
            "http://olx/2": {"free": ["http://ad/2"], "next": "http://olx/1"},
        }

        driver, ads, outcomes = self.collect(pages, "http://olx/1")

        self.assertEqual(self.links_of(ads), ["http://ad/1", "http://ad/2"])
        self.assertEqual(
            outcomes,
            [
                PageOutcome(1, OUTCOME_OK),
                PageOutcome(2, OUTCOME_FAILED, REASON_PAGINATION_LOOP),
            ],
        )
        # The loop must be detected before revisiting the first page.
        self.assertEqual(driver.requested, ["http://olx/1", "http://olx/2"])

    def test_pagination_loop_on_repeated_page_number_stops(self):
        pages = {
            "http://olx/list?page=1": {
                "free": ["http://ad/1"],
                "next": "http://olx/list?page=2",
            },
            "http://olx/list?page=2": {
                "free": ["http://ad/2"],
                "next": "http://olx/list?page=2&extra=1",
            },
        }

        driver, ads, outcomes = self.collect(pages, "http://olx/list?page=1")

        self.assertEqual(self.links_of(ads), ["http://ad/1", "http://ad/2"])
        self.assertEqual(
            outcomes,
            [
                PageOutcome(1, OUTCOME_OK),
                PageOutcome(2, OUTCOME_FAILED, REASON_PAGINATION_LOOP),
            ],
        )
        self.assertEqual(
            driver.requested, ["http://olx/list?page=1", "http://olx/list?page=2"]
        )

    def test_broken_next_page_link_is_pagination_failure(self):
        pages = {
            "http://olx/1": {"free": ["http://ad/1"], "next": BROKEN_NEXT},
        }

        _, ads, outcomes = self.collect(pages, "http://olx/1")

        self.assertEqual(self.links_of(ads), ["http://ad/1"])
        self.assertEqual(
            outcomes, [PageOutcome(1, OUTCOME_FAILED, REASON_PAGINATION_FAILED)]
        )

    def test_exclude_free_and_paid_produces_skipped_outcome(self):
        pages = {"http://olx/1": {"free": ["http://ad/1"], "next": None}}

        driver, ads, outcomes = self.collect(
            pages, "http://olx/1", exclude_free=True, exclude_paid=True
        )

        self.assertEqual(ads, [])
        self.assertEqual(outcomes, [PageOutcome(1, OUTCOME_SKIPPED, REASON_EXCLUDED)])
        self.assertEqual(driver.requested, [])

    def test_exclude_free_still_collects_paid(self):
        pages = {
            "http://olx/1": {
                "free": ["http://ad/1"],
                "paid": ["http://ad/2"],
                "next": None,
            },
        }

        _, ads, outcomes = self.collect(pages, "http://olx/1", exclude_free=True)

        self.assertEqual(self.links_of(ads), ["http://ad/2"])
        self.assertEqual(ads[0]["kind"], "paid")
        self.assertEqual(outcomes, [PageOutcome(1, OUTCOME_END)])

    def test_format_page_outcome(self):
        self.assertEqual(format_page_outcome(PageOutcome(1, OUTCOME_OK)), "page 1: ok")
        self.assertEqual(
            format_page_outcome(PageOutcome(2, OUTCOME_END)), "page 2: end"
        )
        self.assertEqual(
            format_page_outcome(PageOutcome(3, OUTCOME_FAILED, REASON_NO_ADS)),
            "page 3: failed (no ad cards found)",
        )


class AdsCommandTestCase(unittest.TestCase):
    def setUp(self):
        inject.clear()

    def invoke_ads(self, pages, args):
        driver = FakeDriver(pages)

        @contextmanager
        def fake_chrome(headless=False, proxy=None):
            yield driver

        with mock.patch.object(olx, "chrome", fake_chrome):
            return CliRunner().invoke(olx.cli, ["ads"] + args)

    def test_page_outcomes_go_to_stderr_and_csv_stays_on_stdout(self):
        pages = {
            "http://olx.example.com/1": {
                "free": ["http://ad.example.com/1"],
                "next": "http://olx.example.com/2",
            },
            "http://olx.example.com/2": {"fail": True},
        }

        result = self.invoke_ads(pages, ["--all", "http://olx.example.com/1"])

        self.assertEqual(result.exit_code, 0, result.output)

        # stdout carries only the CSV document.
        self.assertIn("http://ad.example.com/1", result.stdout)
        self.assertNotIn("page 1:", result.stdout)
        self.assertNotIn("page 2:", result.stdout)

        # stderr carries one verifiable outcome line per page.
        self.assertIn("page 1: ok", result.stderr)
        self.assertIn("page 2: failed (page load failed)", result.stderr)

    def test_no_free_and_no_paid_emits_skipped_outcome(self):
        pages = {
            "http://olx.example.com/1": {
                "free": ["http://ad.example.com/1"],
                "next": None,
            },
        }

        result = self.invoke_ads(
            pages, ["--all", "--no-free", "--no-paid", "http://olx.example.com/1"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("http://ad.example.com/1", result.stdout)
        self.assertIn("page 1: skipped (free and paid ads excluded)", result.stderr)


if __name__ == "__main__":
    unittest.main()
