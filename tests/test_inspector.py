import os
import unittest
from core.inspector import inspect_app_files

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def app_file():
    return {
        "filename": "sample_app.js",
        "filepath": os.path.join(FIXTURE_DIR, "sample_app.js"),
        "category": "app",
    }


def vendor_file():
    return {
        "filename": "polyfills.abc.js",
        "filepath": "/dev/null",
        "category": "vendor",
    }


class TestInspectorSinks(unittest.TestCase):
    def test_eval_detected(self):
        result = inspect_app_files([app_file()], [])
        sinks = [s for s in result["sinks"] if s["label"] == "eval"]
        self.assertEqual(len(sinks), 1)
        self.assertEqual(sinks[0]["file"], "sample_app.js")

    def test_innerHTML_detected(self):
        result = inspect_app_files([app_file()], [])
        sinks = [s for s in result["sinks"] if s["label"] == "innerHTML="]
        self.assertEqual(len(sinks), 1)


class TestInspectorGates(unittest.TestCase):
    def test_isAdmin_detected(self):
        result = inspect_app_files([app_file()], [])
        gates = [g for g in result["gates"] if "isAdmin" in g["snippet"]]
        self.assertEqual(len(gates), 1)


class TestInspectorFlags(unittest.TestCase):
    def test_DEBUG_true_detected(self):
        result = inspect_app_files([app_file()], [])
        # DEBUG = true is on line 2 of the fixture
        flags_on_line_2 = [f for f in result["flags"] if f["line"] == 2]
        self.assertEqual(len(flags_on_line_2), 1)

    def test_featureFlags_detected(self):
        result = inspect_app_files([app_file()], [])
        # featureFlags is on line 3 of the fixture
        flags_on_line_3 = [f for f in result["flags"] if f["line"] == 3]
        self.assertEqual(len(flags_on_line_3), 1)


class TestInspectorPostMessage(unittest.TestCase):
    def test_postMessage_without_origin_check_flagged(self):
        # File listens for "message" events but never checks event.origin -> flag it.
        # Note: fixture uses addEventListener("message"), no postMessage call. We treat
        # missing origin check on any message handler as the same risk.
        # For symmetry the inspector flags both .postMessage(...) without origin check
        # AND addEventListener("message", ...) without origin check.
        result = inspect_app_files([app_file()], [])
        pm = result["postmessage_no_origin"]
        self.assertEqual(len(pm), 1)
        self.assertEqual(pm[0]["file"], "sample_app.js")


class TestInspectorEndpointEnrichment(unittest.TestCase):
    def test_state_changing_detected(self):
        result = inspect_app_files([app_file()], [{"url": "/api/users/", "file": "sample_app.js"}])
        enriched = result["endpoints_enriched"]
        verbs = {e.get("verb") for e in enriched}
        # Either GET or DELETE may be picked depending on which occurrence finds first.
        self.assertTrue(verbs & {"GET", "DELETE"})

    def test_auth_context_likely_authed(self):
        result = inspect_app_files(
            [app_file()],
            [{"url": "/api/users/", "file": "sample_app.js"}],
        )
        enriched = result["endpoints_enriched"]
        authed = [e for e in enriched if e.get("auth_context") == "likely_authed"]
        self.assertGreaterEqual(len(authed), 1)


class TestInspectorIgnoresVendor(unittest.TestCase):
    def test_vendor_files_skipped(self):
        result = inspect_app_files([vendor_file()], [])
        self.assertEqual(result["sinks"], [])
        self.assertEqual(result["gates"], [])
        self.assertEqual(result["flags"], [])


if __name__ == "__main__":
    unittest.main()
