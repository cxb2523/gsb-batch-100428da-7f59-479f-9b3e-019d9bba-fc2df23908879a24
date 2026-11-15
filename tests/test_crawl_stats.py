"""Tests for crawl outcome statistics on the Scanner.

Verifies that crawl() accumulates verifiable counters (pages visited,
succeeded, failed, links ignored, links truncated, endpoints discovered)
without changing target_links contents/order or ignore/max_links behavior.
"""

import pytest
import requests
from unittest.mock import Mock, patch

from scanner import Scanner


def _crawl_with_links(scanner, links_by_url):
    """Run crawl with extract_links_from stubbed by URL."""
    def fake_extract(url):
        return links_by_url.get(url, [])

    with patch.object(scanner, "extract_links_from", side_effect=fake_extract):
        scanner.crawl()


class TestCrawlStatsBasic:
    """Normal crawl: statistics are consistent with target_links."""

    def test_stats_match_target_links(self, scanner):
        links_by_url = {
            "http://example.com": [
                "http://example.com/page1",
                "http://example.com/page2",
            ],
            "http://example.com/page1": ["http://example.com/page3"],
            "http://example.com/page2": [],
            "http://example.com/page3": [],
        }
        _crawl_with_links(scanner, links_by_url)

        stats = scanner.get_crawl_stats()
        assert scanner.target_links == [
            "http://example.com/page1",
            "http://example.com/page2",
            "http://example.com/page3",
        ]
        assert stats["endpoints_discovered"] == len(scanner.target_links) == 3
        # Seed page plus every discovered page was visited and parsed
        assert stats["pages_visited"] == 4
        assert stats["pages_succeeded"] == 4
        assert stats["pages_failed"] == 0
        assert stats["links_ignored"] == 0
        assert stats["links_truncated"] == 0

    def test_stats_initially_zero(self, scanner):
        stats = scanner.get_crawl_stats()
        assert stats == {
            "pages_visited": 0,
            "pages_succeeded": 0,
            "pages_failed": 0,
            "links_ignored": 0,
            "links_truncated": 0,
            "endpoints_discovered": 0,
        }

    def test_stats_readable_after_run_scanner(self, scanner):
        """run_scanner must not reset or corrupt the crawl statistics."""
        _crawl_with_links(
            scanner,
            {"http://example.com": ["http://example.com/page1"]},
        )
        before = scanner.get_crawl_stats()

        with patch.object(scanner, "_scan_single_endpoint"):
            scanner.run_scanner(threads=1)

        assert scanner.get_crawl_stats() == before

    def test_crawl_accumulates_until_reset(self, scanner):
        links = {"http://example.com": ["http://example.com/page1"]}
        _crawl_with_links(scanner, links)
        first = scanner.get_crawl_stats()

        # Second crawl: page1 is already known, only the seed is revisited
        _crawl_with_links(scanner, links)
        second = scanner.get_crawl_stats()

        assert second["pages_visited"] == first["pages_visited"] + 1
        assert second["endpoints_discovered"] == first["endpoints_discovered"]

        scanner.reset_crawl_stats()
        assert all(v == 0 for v in scanner.get_crawl_stats().values())


class TestCrawlStatsIgnoreRules:
    """Ignore-rule hits increment the skipped counter and stay unqueued."""

    def test_ignored_links_counted_and_not_discovered(self):
        scanner = Scanner(
            "http://example.com",
            ignore_links=["/logout", "http://example.com/admin*", "/private"],
        )
        links_by_url = {
            "http://example.com": [
                "http://example.com/logout",       # path match
                "http://example.com/admin/panel",  # glob match
                "http://example.com/private",      # exact/path match
                "http://example.com/page1",
            ],
            "http://example.com/page1": [],
        }
        _crawl_with_links(scanner, links_by_url)

        stats = scanner.get_crawl_stats()
        assert stats["links_ignored"] == 3
        assert scanner.target_links == ["http://example.com/page1"]
        assert stats["endpoints_discovered"] == 1
        # Ignored links must never be enqueued/visited
        assert stats["pages_visited"] == 2  # seed + page1

    def test_ignore_semantics_unchanged(self):
        """Duplicates are not counted as ignored; only rule hits are."""
        scanner = Scanner("http://example.com", ignore_links=["/logout"])
        links_by_url = {
            "http://example.com": [
                "http://example.com/logout",
                "http://example.com/logout",  # duplicate of an ignored link
                "http://example.com/page1",
                "http://example.com/page1",   # duplicate of a discovered link
            ],
            "http://example.com/page1": [],
        }
        _crawl_with_links(scanner, links_by_url)

        stats = scanner.get_crawl_stats()
        assert stats["links_ignored"] == 2  # each encounter with the rule hit
        assert scanner.target_links == ["http://example.com/page1"]


class TestCrawlStatsMaxLinks:
    """max_links truncation is counted, including queued-but-unprocessed."""

    def test_truncated_links_counted(self):
        scanner = Scanner("http://example.com", max_links=2)
        links_by_url = {
            "http://example.com": [
                "http://example.com/page1",
                "http://example.com/page2",
                "http://example.com/page3",  # over the cap on this page
            ],
            "http://example.com/page1": ["http://example.com/page4"],
            "http://example.com/page2": [],
        }
        _crawl_with_links(scanner, links_by_url)

        stats = scanner.get_crawl_stats()
        # Cap behavior unchanged: exactly max_links endpoints discovered
        assert scanner.target_links == [
            "http://example.com/page1",
            "http://example.com/page2",
        ]
        assert stats["endpoints_discovered"] == 2
        # page3 (not enqueued) + page1/page2 (enqueued, never processed)
        assert stats["links_truncated"] > 0
        assert stats["links_truncated"] == 3
        # Only the seed page was fetched before the cap stopped the crawl
        assert stats["pages_visited"] == 1

    def test_no_truncation_when_unlimited(self, scanner):
        assert scanner.max_links == 0
        _crawl_with_links(
            scanner,
            {"http://example.com": ["http://example.com/page1"]},
        )
        assert scanner.get_crawl_stats()["links_truncated"] == 0


class TestCrawlStatsFailures:
    """A failing page increments the failure counter; the crawl continues."""

    def test_failed_page_counted_and_crawl_continues(self, scanner):
        links_by_url = {
            "http://example.com": [
                "http://example.com/broken",
                "http://example.com/page1",
            ],
            "http://example.com/page1": ["http://example.com/page2"],
            "http://example.com/page2": [],
        }

        def fake_extract(url):
            if url == "http://example.com/broken":
                raise requests.exceptions.ConnectionError("boom")
            return links_by_url.get(url, [])

        with patch.object(scanner, "extract_links_from", side_effect=fake_extract):
            scanner.crawl()

        stats = scanner.get_crawl_stats()
        assert stats["pages_failed"] == 1
        assert stats["pages_visited"] == 4
        assert stats["pages_succeeded"] == 3
        # Crawl continued past the failure and discovered everything else
        assert scanner.target_links == [
            "http://example.com/broken",
            "http://example.com/page1",
            "http://example.com/page2",
        ]
        assert stats["endpoints_discovered"] == 3

    def test_request_exception_recorded_via_extract_links(self, scanner):
        """Real extract_links_from records the failure when the request dies."""
        with patch.object(
            scanner.session, "get",
            side_effect=requests.exceptions.ConnectionError("down"),
        ):
            scanner.crawl()

        stats = scanner.get_crawl_stats()
        assert stats["pages_visited"] == 1
        assert stats["pages_failed"] == 1
        assert stats["pages_succeeded"] == 0
        assert scanner.target_links == []

    def test_timeout_recorded_as_failure(self, scanner):
        with patch.object(
            scanner.session, "get",
            side_effect=requests.exceptions.Timeout("slow"),
        ):
            scanner.crawl()

        assert scanner.get_crawl_stats()["pages_failed"] == 1


class TestCrawlStatsThreadSafety:
    """Counters stay consistent while the thread pool scans concurrently."""

    def test_stats_stable_during_threaded_scan(self, scanner):
        _crawl_with_links(
            scanner,
            {
                "http://example.com": [
                    f"http://example.com/page{i}" for i in range(5)
                ],
            },
        )
        before = scanner.get_crawl_stats()

        with patch.object(scanner, "_scan_single_endpoint"):
            scanner.run_scanner(threads=4)

        assert scanner.get_crawl_stats() == before

    def test_snapshot_is_a_copy(self, scanner):
        snapshot = scanner.get_crawl_stats()
        snapshot["pages_visited"] = 999
        assert scanner.get_crawl_stats()["pages_visited"] == 0
