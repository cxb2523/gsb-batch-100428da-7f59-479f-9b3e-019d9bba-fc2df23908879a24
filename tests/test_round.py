"""Round-level tests for the Ofek-bot check flow.

Run with either:
    python3 -m unittest discover -s tests -v
    python3 -m pytest tests
"""

import unittest

from support import (
    APP,
    FakeBrowser,
    FakeCrawler,
    FakeNotifier,
    counters,
    kid,
    title_for,
)


class NormalRoundTests(unittest.TestCase):
    def test_normal_round_notifies_only_kids_with_tasks(self):
        kids = [kid("Dana"), kid("Noa"), kid("Amit")]
        scraper = FakeCrawler(
            {
                "dana": counters(todo="3", tofix="0", checked="7", waiting="1"),
                "noa": counters(todo="0", tofix="0", checked="2", waiting="0"),
                "amit": counters(todo="0", tofix="4", checked="1", waiting="9"),
            }
        )
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, kids, notifier)

        self.assertEqual(
            [title for title, _ in notifier.sent],
            [title_for("Dana"), title_for("Amit")],
        )
        self.assertEqual(notifier.sent[0][1], "3\n0\n7\n1")
        self.assertEqual(notifier.sent[1][1], "0\n4\n1\n9")
        self.assertEqual(
            (summary.total, summary.succeeded, summary.failed), (3, 3, 0)
        )
        self.assertEqual(
            (summary.with_tasks, summary.without_tasks), (2, 1)
        )
        self.assertEqual(summary.failures, [])

        # Every kid got an independent browser, all closed before the round ends.
        self.assertEqual(len(scraper.opened), 3)
        self.assertEqual(len({id(browser) for browser in scraper.opened}), 3)
        self.assertEqual(scraper.closed, scraper.opened)
        self.assertTrue(all(browser.quit_called for browser in scraper.opened))
        self.assertIsNone(scraper.browser)

    def test_notification_keeps_title_and_four_line_raw_format(self):
        raw = {
            "todo": "2 משימות לביצוע",
            "tofix": "1 משימה לתיקון",
            "checked": "7 משימות שנבדקו",
            "waiting": "0 משימות לבדיקה",
        }
        notifier = FakeNotifier()

        APP.check_all(FakeCrawler({"dana": raw}), [kid("Dana")], notifier)

        self.assertEqual(
            notifier.sent,
            [
                (
                    "מצב משימות אופק של Dana",
                    "2 משימות לביצוע\n1 משימה לתיקון\n"
                    "7 משימות שנבדקו\n0 משימות לבדיקה",
                )
            ],
        )


class FailureIsolationTests(unittest.TestCase):
    def test_single_failure_continues_with_independent_counts(self):
        kids = [kid("Dana"), kid("Noa"), kid("Amit")]
        scraper = FakeCrawler(
            {
                "dana": counters(todo="5", tofix="1", checked="0", waiting="0"),
                "noa": RuntimeError("login page changed"),
                "amit": counters(todo="0", tofix="0", checked="8", waiting="2"),
            }
        )
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, kids, notifier)

        # Failed kid is never notified and never inherits Dana's counters.
        self.assertEqual([title for title, _ in notifier.sent], [title_for("Dana")])
        self.assertEqual(
            (summary.total, summary.succeeded, summary.failed), (3, 2, 1)
        )
        self.assertEqual(
            (summary.with_tasks, summary.without_tasks), (1, 1)
        )
        self.assertEqual([name for name, _ in summary.failures], ["Noa"])
        self.assertIn("login page changed", summary.failures[0][1])

        # All three kids were attempted, and every browser was closed.
        self.assertEqual(
            [username for username, _ in scraper.crawled],
            ["dana", "noa", "amit"],
        )
        self.assertEqual(len(scraper.opened), 3)
        self.assertEqual(len(scraper.closed), 3)
        self.assertTrue(all(browser.quit_called for browser in scraper.opened))

    def test_empty_fields_skip_entry_and_continue(self):
        kids = [
            kid("Dana"),
            {"name": "", "username": "", "password": ""},
            {"name": "Noa", "username": "noa", "password": None},
            kid("Amit"),
            kid("Roni"),
        ]
        scraper = FakeCrawler(
            {
                "dana": counters(todo="1"),
                "amit": counters(todo="0"),
                "roni": counters(todo="2"),
            }
        )
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, kids, notifier)

        # Invalid entries never reach the browser and never notify.
        self.assertEqual(
            [username for username, _ in scraper.crawled],
            ["dana", "amit", "roni"],
        )
        self.assertEqual(
            [title for title, _ in notifier.sent],
            [title_for("Dana"), title_for("Roni")],
        )
        self.assertEqual(
            (summary.total, summary.succeeded, summary.failed), (5, 3, 2)
        )
        self.assertEqual(
            (summary.with_tasks, summary.without_tasks), (2, 1)
        )
        self.assertEqual([name for name, _ in summary.failures], ["#2", "Noa"])
        self.assertIn(
            "missing name, username, password", summary.failures[0][1]
        )
        self.assertIn("missing password", summary.failures[1][1])
        self.assertEqual(len(scraper.opened), 3)
        self.assertTrue(all(browser.quit_called for browser in scraper.opened))

    def test_unparseable_counter_fails_only_that_kid(self):
        kids = [kid("Dana"), kid("Noa")]
        scraper = FakeCrawler(
            {
                "dana": counters(todo="not a number", tofix="0"),
                "noa": counters(todo="0", tofix="0"),
            }
        )
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, kids, notifier)

        self.assertEqual(notifier.sent, [])
        self.assertEqual((summary.succeeded, summary.failed), (1, 1))
        self.assertEqual(
            (summary.with_tasks, summary.without_tasks), (0, 1)
        )
        self.assertEqual(summary.failures[0][0], "Dana")
        self.assertIn("todo", summary.failures[0][1])
        self.assertIn("not a number", summary.failures[0][1])
        self.assertEqual(
            [username for username, _ in scraper.crawled], ["dana", "noa"]
        )
        self.assertTrue(all(browser.quit_called for browser in scraper.opened))

    def test_missing_counter_key_fails_without_reusing_previous_kid(self):
        kids = [kid("Dana"), kid("Noa")]
        scraper = FakeCrawler(
            {
                "dana": counters(todo="9", tofix="9", checked="9", waiting="9"),
                # waiting is missing for Noa.
                "noa": {"todo": "1", "tofix": "0", "checked": "0"},
            }
        )
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, kids, notifier)

        self.assertEqual([title for title, _ in notifier.sent], [title_for("Dana")])
        self.assertEqual(summary.failures[0][0], "Noa")
        self.assertIn("waiting", summary.failures[0][1])
        self.assertEqual(summary.with_tasks, 1)

    def test_crawl_error_reason_names_the_failed_step(self):
        kids = [kid("Dana"), kid("Noa")]
        scraper = FakeCrawler(
            {
                "dana": APP.CrawlError("read tofix counter failed: boom"),
                "noa": counters(todo="1"),
            }
        )
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, kids, notifier)

        self.assertEqual([title for title, _ in notifier.sent], [title_for("Noa")])
        self.assertIn("read tofix counter failed: boom", summary.failures[0][1])
        self.assertTrue(all(browser.quit_called for browser in scraper.opened))

    def test_browser_is_closed_when_scrape_raises(self):
        class ExplodingCrawler(FakeCrawler):
            def crowl(self, username, password):
                raise APP.CrawlError("enter username failed: no such element")

        scraper = ExplodingCrawler({})

        summary = APP.check_all(scraper, [kid("Dana")], FakeNotifier())

        self.assertEqual(summary.failed, 1)
        self.assertEqual(len(scraper.opened), 1)
        self.assertTrue(scraper.opened[0].quit_called)

    def test_browser_init_failure_does_not_stop_round(self):
        class PartiallyBrokenCrawler(FakeCrawler):
            def __init__(self, results):
                super().__init__(results)
                self.calls = 0

            def init_browser(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("ChromeDriver download failed")
                return super().init_browser()

        scraper = PartiallyBrokenCrawler({"noa": counters(todo="1")})
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, [kid("Dana"), kid("Noa")], notifier)

        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.failures[0][0], "Dana")
        self.assertIn("ChromeDriver download failed", summary.failures[0][1])
        self.assertEqual([title for title, _ in notifier.sent], [title_for("Noa")])

    def test_notification_failure_does_not_stop_round(self):
        kids = [kid("Dana"), kid("Noa")]
        scraper = FakeCrawler(
            {"dana": counters(todo="1"), "noa": counters(todo="1")}
        )
        notifier = FakeNotifier(fail_for=[title_for("Dana")])

        summary = APP.check_all(scraper, kids, notifier)

        # Dana's scrape still counts as a success; Noa is still notified.
        self.assertEqual((summary.succeeded, summary.failed), (2, 0))
        self.assertEqual([title for title, _ in notifier.sent], [title_for("Noa")])

    def test_leftover_browser_is_closed_at_round_end(self):
        class LeakyCrawler(FakeCrawler):
            """Leaves a browser tracked on the crawler after the kid is done.

            This mirrors a crash between creating a session and clearing it, so
            the round itself must make sure nothing is left running.
            """

            def init_browser(self):
                browser = super().init_browser()
                self.browser = browser
                # Hand back a different object so the per-kid cleanup cannot
                # clear the one tracked on the crawler.
                return FakeBrowser()

            def close_browser(self, browser=None):
                driver = browser if browser is not None else self.browser
                self.closed.append(driver)
                driver.quit()
                if driver is self.browser:
                    self.browser = None

        scraper = LeakyCrawler({})
        scraper.results["dana"] = counters(todo="0", tofix="0")

        summary = APP.check_all(scraper, [kid("Dana")], FakeNotifier())

        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(len(scraper.opened), 1)
        # Both the per-kid browser and the leftover tracked one were quit.
        self.assertEqual(len(scraper.closed), 2)
        self.assertTrue(all(driver.quit_called for driver in scraper.closed))
        self.assertIs(scraper.closed[-1], scraper.opened[0])
        self.assertIsNone(scraper.browser)


class RealCrawlerIntegrationTests(unittest.TestCase):
    """Exercise the real Crawler lifecycle with only Selenium stubbed out."""

    def make_crawler(self, scrape):
        import os
        from unittest import mock

        class StubbedCrawler(APP.Crawler):
            def __init__(self):
                with mock.patch.dict(os.environ, {"NOTIFIERS": ""}), mock.patch.object(
                    APP.Crawler, "get_kids"
                ):
                    super().__init__()
                self.kids = []
                self.started = []

            def init_browser(self):
                browser = FakeBrowser()
                self.started.append(browser)
                self.browser = browser
                return browser

            def crowl(self, username, password):
                return scrape(username, password)

        return StubbedCrawler()

    def test_real_close_browser_runs_for_each_kid(self):
        def scrape(username, password):
            if username == "noa":
                raise APP.CrawlError("enter username failed: element not found")
            return counters(todo="1")

        crawler = self.make_crawler(scrape)
        notifier = FakeNotifier()

        summary = APP.check_all(crawler, [kid("Dana"), kid("Noa")], notifier)

        self.assertEqual((summary.succeeded, summary.failed), (1, 1))
        self.assertEqual(len(crawler.started), 2)
        self.assertTrue(all(browser.quit_called for browser in crawler.started))
        self.assertIsNone(crawler.browser)
        self.assertEqual([title for title, _ in notifier.sent], [title_for("Dana")])


class SummaryTests(unittest.TestCase):
    def test_all_success_summary_reports_totals(self):
        kids = [kid("Dana"), kid("Noa")]
        scraper = FakeCrawler(
            {
                "dana": counters(todo="0", tofix="0"),
                "noa": counters(todo="0", tofix="0"),
            }
        )
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, kids, notifier)

        self.assertEqual(notifier.sent, [])
        self.assertEqual(
            (summary.total, summary.succeeded, summary.failed), (2, 2, 0)
        )
        self.assertEqual(
            (summary.with_tasks, summary.without_tasks), (0, 2)
        )
        message = summary.message()
        self.assertIn("2 succeeded", message)
        self.assertIn("0 failed", message)
        self.assertIn("0 with tasks", message)
        self.assertIn("2 without tasks", message)
        self.assertNotIn("failures", message)

    def test_round_summary_lists_failure_names_and_reasons(self):
        kids = [kid("Dana"), kid("Noa")]
        scraper = FakeCrawler({"dana": counters(todo="1"), "noa": TimeoutError()})

        summary = APP.check_all(scraper, kids, FakeNotifier())

        message = summary.message()
        self.assertIn("1 succeeded", message)
        self.assertIn("1 failed", message)
        self.assertIn("1 with tasks", message)
        self.assertIn("0 without tasks", message)
        self.assertIn("Noa:", message)
        self.assertIn("TimeoutError", message)
        self.assertNotIn("\n", message)

    def test_summary_is_written_to_the_log(self):
        from loguru import logger

        messages = []
        sink_id = logger.add(
            lambda message: messages.append(str(message)), level="INFO"
        )
        try:
            scraper = FakeCrawler(
                {"dana": counters(todo="1"), "noa": RuntimeError("boom")}
            )
            APP.check_all(scraper, [kid("Dana"), kid("Noa")], FakeNotifier())
        finally:
            logger.remove(sink_id)

        summary_lines = [m for m in messages if "Round summary" in m]
        self.assertEqual(len(summary_lines), 1)
        self.assertIn("1 succeeded", summary_lines[0])
        self.assertIn("1 failed", summary_lines[0])
        self.assertIn("Noa:", summary_lines[0])
        self.assertIn("boom", summary_lines[0])

    def test_empty_kids_list_produces_a_zero_summary(self):
        summary = APP.check_all(FakeCrawler({}), [], FakeNotifier())

        self.assertEqual(
            (
                summary.total,
                summary.succeeded,
                summary.failed,
                summary.with_tasks,
                summary.without_tasks,
            ),
            (0, 0, 0, 0, 0),
        )
        self.assertEqual(summary.failures, [])
        self.assertIn("0 succeeded", summary.message())


class ConfigTests(unittest.TestCase):
    def test_loads_config_and_skips_blank_entries(self):
        import os
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(os.path.join(temp_dir, "config"))
            with open(
                os.path.join(temp_dir, "config", "config.yaml"),
                "w",
                encoding="utf-8",
            ) as config_file:
                config_file.write(
                    "kids:\n"
                    "  - name: Dana\n"
                    "    username: dana\n"
                    "    password: pw\n"
                    "  - name:\n"
                    "    username:\n"
                    "    password:\n"
                    "  - name: Noa\n"
                    "    username: noa\n"
                    "    password: pw\n"
                )
            with mock.patch.dict(os.environ, {"NOTIFIERS": ""}):
                original_cwd = os.getcwd()
                os.chdir(temp_dir)
                try:
                    crawler = APP.Crawler()
                finally:
                    os.chdir(original_cwd)

        self.assertEqual(len(crawler.kids), 3)

        scraper = FakeCrawler(
            {"dana": counters(todo="1"), "noa": counters(todo="0", tofix="0")}
        )
        notifier = FakeNotifier()

        summary = APP.check_all(scraper, crawler.kids, notifier)

        self.assertEqual(
            (summary.total, summary.succeeded, summary.failed), (3, 2, 1)
        )
        self.assertEqual(
            (summary.with_tasks, summary.without_tasks), (1, 1)
        )
        self.assertEqual(summary.failures[0][0], "#2")
        self.assertEqual([title for title, _ in notifier.sent], [title_for("Dana")])
        self.assertTrue(all(browser.quit_called for browser in scraper.opened))


class HelperTests(unittest.TestCase):
    def test_has_tasks_uses_todo_and_tofix_counts(self):
        self.assertTrue(APP.has_tasks("2 משימות", "0"))
        self.assertTrue(APP.has_tasks("0", "1 משימות"))
        self.assertFalse(APP.has_tasks("0", "0"))

    def test_parse_counter_and_parse_counts(self):
        self.assertEqual(APP.parse_counter("todo", "12 משימות"), 12)
        with self.assertRaises(APP.CountParseError):
            APP.parse_counter("todo", None)
        with self.assertRaises(APP.CountParseError):
            APP.parse_counter("todo", "אין")
        with self.assertRaises(APP.CountParseError):
            APP.parse_counts(None)

        counts = APP.parse_counts(
            {"todo": "1", "tofix": "2", "checked": "3", "waiting": "4"}
        )
        self.assertEqual(
            counts, {"todo": 1, "tofix": 2, "checked": 3, "waiting": 4}
        )

    def test_main_accepts_kids_directly(self):
        scraper = FakeCrawler({"dana": counters(todo="1")})
        notifier = FakeNotifier()

        summary = APP.main(crawler=scraper, notifier=notifier, kids=[kid("Dana")])

        self.assertEqual(summary.succeeded, 1)
        self.assertEqual([title for title, _ in notifier.sent], [title_for("Dana")])


if __name__ == "__main__":
    unittest.main()
