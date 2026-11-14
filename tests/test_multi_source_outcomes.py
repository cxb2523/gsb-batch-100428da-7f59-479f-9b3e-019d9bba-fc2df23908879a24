"""Acceptance tests for partial-failure handling in the multi-source fan-out.

Fake adapters stand in for real portals: one returns rows, one raises, one
blocks past the shared timeout. The merged list must only contain rows from
the healthy portal(s), de-duplicated on tender_id, while every source gets an
explicit outcome explaining its fate.
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import scraper  # noqa: E402
from gemsentry.sources.base import BaseAdapter  # noqa: E402
from gemsentry.sources.registry import (  # noqa: E402
    SOURCE_STATUS_EMPTY,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_SKIPPED,
    SOURCE_STATUS_SUCCESS,
    SOURCE_STATUS_TIMEOUT,
    SourceRegistry,
)


class OkAdapter(BaseAdapter):
    """Healthy portal: returns its canned rows immediately."""

    implemented = True

    def __init__(self, source_config, rows):
        super().__init__(source_config)
        self.rows = rows

    def fetch_active_tenders(self, keywords, max_pages=5):
        return [self.normalize_tender(**row) for row in self.rows]


class BoomAdapter(BaseAdapter):
    """Portal whose request blows up; only this source may be affected."""

    implemented = True

    def fetch_active_tenders(self, keywords, max_pages=5):
        raise RuntimeError("portal exploded")


class BlockingAdapter(BaseAdapter):
    """Portal that never answers until its release event is set."""

    implemented = True

    def __init__(self, source_config, release_event):
        super().__init__(source_config)
        self.release_event = release_event
        self.ever_returned = False

    def fetch_active_tenders(self, keywords, max_pages=5):
        # Bounded wait so a leaked thread cannot hang the whole test process;
        # the test releases the event in tearDown once the timeout is proven.
        self.release_event.wait(timeout=30)
        self.ever_returned = True
        return []


def _source(source_id, name=None, engine="gepnic", **extra):
    cfg = {"id": source_id, "name": name or source_id.title(),
           "url": f"https://{source_id}.example/app", "engine": engine,
           "enabled": True}
    cfg.update(extra)
    return cfg


def _registry(adapters, sources):
    """Build a registry without touching config/sources.json on disk."""
    registry = SourceRegistry.__new__(SourceRegistry)
    registry.sources = sources
    registry.adapters = {a.source_id: a for a in adapters}
    registry.last_fetch_outcomes = {}
    return registry


class TestMultiSourcePartialFailure(unittest.TestCase):
    def setUp(self):
        self.release_event = threading.Event()
        self.sources = [
            _source("p_ok", "Healthy Portal"),
            _source("p_boom", "Exploding Portal"),
            _source("p_slow", "Stuck Portal"),
        ]
        self.registry = _registry(
            [
                OkAdapter(self.sources[0], [
                    {"tender_id": "T1", "title": "Supply of drones", "buyer_org": "Navy"},
                    {"tender_id": "T2", "title": "Runway repair", "buyer_org": "IAF"},
                ]),
                BoomAdapter(self.sources[1]),
                BlockingAdapter(self.sources[2], self.release_event),
            ],
            self.sources,
        )
        self.workspace = tempfile.mkdtemp(prefix="gemsentry_outcomes_")

    def tearDown(self):
        # Let the timed-out worker thread finish so it cannot outlive the test.
        self.release_event.set()
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _outcomes_by_id(self, result):
        return {o.source_id: o for o in result.outcomes}

    def test_healthy_failed_and_timed_out_sources_each_get_an_outcome(self):
        result = self.registry.fetch_with_outcomes(["drone"], timeout=0.5)

        by_id = self._outcomes_by_id(result)
        self.assertEqual(by_id["p_ok"].status, SOURCE_STATUS_SUCCESS)
        self.assertEqual(by_id["p_ok"].count, 2)

        self.assertEqual(by_id["p_boom"].status, SOURCE_STATUS_FAILED)
        self.assertIn("RuntimeError", by_id["p_boom"].reason)
        self.assertIn("portal exploded", by_id["p_boom"].reason)
        self.assertEqual(by_id["p_boom"].count, 0)

        self.assertEqual(by_id["p_slow"].status, SOURCE_STATUS_TIMEOUT)
        self.assertIn("timed out", by_id["p_slow"].reason)
        self.assertEqual(by_id["p_slow"].count, 0)

        # Ledger order follows the dispatched adapters, not completion order.
        self.assertEqual([o.source_id for o in result.outcomes],
                         ["p_ok", "p_boom", "p_slow"])

    def test_merged_tenders_come_only_from_the_healthy_portal(self):
        result = self.registry.fetch_with_outcomes(["drone"], timeout=0.5)
        self.assertEqual({t["tender_id"] for t in result.tenders}, {"T1", "T2"})
        self.assertTrue(all(t["source_id"] == "p_ok" for t in result.tenders))

    def test_list_api_still_returns_plain_tenders_and_records_outcomes(self):
        # Existing callers keep the list-shaped return value...
        tenders = self.registry.fetch_from_all_active(["drone"], timeout=0.5)
        self.assertIsInstance(tenders, list)
        self.assertEqual({t["tender_id"] for t in tenders}, {"T1", "T2"})

        # ...and can reach the same ledger through the accessor afterwards.
        ledger = {o.source_id: o.status
                  for o in self.registry.get_last_fetch_outcomes()}
        self.assertEqual(ledger, {
            "p_ok": SOURCE_STATUS_SUCCESS,
            "p_boom": SOURCE_STATUS_FAILED,
            "p_slow": SOURCE_STATUS_TIMEOUT,
        })

    def test_partial_failure_does_not_break_ingest(self):
        tenders = self.registry.fetch_from_all_active(["drone"], timeout=0.5)
        new_count = scraper.ingest_external_tenders(
            tenders,
            tenders_dir=self.workspace,
            downloads_dir=os.path.join(self.workspace, "downloads"),
        )
        self.assertEqual(new_count, 2)
        self.assertEqual(
            set(scraper.load_existing_metadata(self.workspace)), {"T1", "T2"}
        )

    def test_outcomes_serialize_to_plain_dicts(self):
        result = self.registry.fetch_with_outcomes(["drone"], timeout=0.5)
        dicts = result.outcomes_as_dicts()
        self.assertTrue(all(isinstance(d, dict) for d in dicts))
        slow = next(d for d in dicts if d["source_id"] == "p_slow")
        self.assertEqual(slow["status"], SOURCE_STATUS_TIMEOUT)
        self.assertTrue(result.has_problems)


class TestCrossPortalDedup(unittest.TestCase):
    def test_first_arrival_wins_for_a_tender_on_two_portals(self):
        sources = [_source("p1"), _source("p2")]
        registry = _registry(
            [
                OkAdapter(sources[0], [
                    {"tender_id": "T1", "title": "Original listing", "buyer_org": "Navy"},
                    {"tender_id": "T2", "title": "Hangar build", "buyer_org": "Navy"},
                ]),
                OkAdapter(sources[1], [
                    # Same tender_id as p1's T1, plus a portal-specific row.
                    {"tender_id": "T1", "title": "Duplicate listing", "buyer_org": "Navy"},
                    {"tender_id": "T3", "title": "Solar rooftop", "buyer_org": "BEL"},
                ]),
            ],
            sources,
        )

        # One worker makes completion order identical to submission order, so
        # first-arrival ownership is deterministic.
        result = registry.fetch_with_outcomes([], max_workers=1)

        ids = [t["tender_id"] for t in result.tenders]
        self.assertEqual(sorted(ids), ["T1", "T2", "T3"])
        self.assertEqual(len(ids), len(set(ids)), "cross-portal duplicate must collapse")

        t1 = next(t for t in result.tenders if t["tender_id"] == "T1")
        self.assertEqual(t1["source_id"], "p1")
        self.assertEqual(t1["title"], "Original listing")

        by_id = {o.source_id: o for o in result.outcomes}
        self.assertEqual(by_id["p1"].status, SOURCE_STATUS_SUCCESS)
        self.assertEqual(by_id["p1"].count, 2)
        # Count is what the adapter returned pre-dedup, not its merged share.
        self.assertEqual(by_id["p2"].count, 2)


class TestNoRunnableAdapters(unittest.TestCase):
    def test_empty_result_without_error_and_skipped_outcome(self):
        # Enabled source whose engine has no adapter implementation.
        sources = [_source("future", "Future Portal", engine="mystery",
                           blocked_reason="CAPTCHA wall")]
        registry = _registry([], sources)

        result = registry.fetch_with_outcomes(["drone"])
        self.assertEqual(result.tenders, [])
        self.assertEqual(len(result.outcomes), 1)

        skipped = result.outcomes[0]
        self.assertEqual(skipped.status, SOURCE_STATUS_SKIPPED)
        self.assertEqual(skipped.source_id, "future")
        self.assertIn("CAPTCHA", skipped.reason)
        self.assertTrue(result.has_problems)

        # The legacy list entry point must likewise stay quiet and empty.
        self.assertEqual(registry.fetch_from_all_active(["drone"]), [])

    def test_completely_empty_registry_returns_empty(self):
        registry = _registry([], [])
        result = registry.fetch_with_outcomes(["drone"])
        self.assertEqual(result.tenders, [])
        self.assertEqual(result.outcomes, [])
        self.assertFalse(result.has_problems)
        self.assertEqual(registry.fetch_from_all_active(["drone"]), [])
        self.assertEqual(registry.get_last_fetch_outcomes(), [])


class TestEmptySuccessIsDistinctFromFailure(unittest.TestCase):
    def test_adapter_returning_zero_rows_is_a_success(self):
        sources = [_source("quiet", "Quiet Portal")]
        registry = _registry([OkAdapter(sources[0], [])], sources)

        result = registry.fetch_with_outcomes(["drone"])
        self.assertEqual(result.tenders, [])
        self.assertEqual(result.outcomes[0].status, SOURCE_STATUS_EMPTY)
        self.assertEqual(result.outcomes[0].count, 0)
        self.assertFalse(result.has_problems)


if __name__ == "__main__":
    unittest.main()
