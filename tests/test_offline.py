#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests for bulk-upload.py's resume and verification decisions.

No network and no archive.org credentials: the remote file-list reader is
replaced by fakes that simulate success, failure, and a partial listing.

Run with:
    python3 -m unittest discover -s tests -v
"""

import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bulk-upload.py"


def load_module():
    """Import bulk-upload.py under a normal module name (as a library)."""
    spec = importlib.util.spec_from_file_location("bulk_upload", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bulk_upload"] = module
    spec.loader.exec_module(module)
    return module


bulk = load_module()


def md5_of(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class OfflineTestCase(unittest.TestCase):
    """Base class: temp config dir, temp upload directory, quiet stdout."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config_dir = self.root / "config"
        self.config_dir.mkdir()
        self.upload_dir = self.root / "files"
        self.upload_dir.mkdir()

        # Redirect all persistent state into the temp directory.
        self._saved = {
            name: getattr(bulk, name)
            for name in ("CONFIG_DIR", "IDENTIFIERS_FILE", "METADATA_FILE",
                         "UPLOAD_LOG_DB", "fetch_ia_files", "quit_flag",
                         "ASSUME_YES", "get_item", "verify_upload")
        }
        bulk.CONFIG_DIR = self.config_dir
        bulk.IDENTIFIERS_FILE = self.config_dir / "identifiers.json"
        bulk.METADATA_FILE = self.config_dir / "metadata.json"
        bulk.UPLOAD_LOG_DB = self.config_dir / "upload_log.db"
        bulk.quit_flag = False
        bulk.ASSUME_YES = True  # never prompt during tests
        bulk.create_upload_log_db()

        # Local files: a.txt and b.txt; c.bin is added by individual tests.
        for name, text in {"a.txt": "alpha", "b.txt": "beta"}.items():
            (self.upload_dir / name).write_text(text, encoding="utf-8")

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(bulk, name, value)
        self._tmp.cleanup()

    def run_capture(self, func, *args, **kwargs):
        """Run func while capturing stdout (and tqdm's stderr), return both."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = func(*args, **kwargs)
        return result, out.getvalue()

    def seed_log(self, identifier, entries):
        """entries: {relative_path: (size, uploaded, md5)}"""
        bulk.update_upload_log(identifier, [
            {
                "relative_path": name,
                "size": size,
                "uploaded": uploaded,
                "md5_hash": md5,
            }
            for name, (size, uploaded, md5) in entries.items()
        ])

    def stub_remote(self, result_or_exception):
        """Install a fake remote reader that returns a dict or raises."""
        def fake(identifier):
            if isinstance(result_or_exception, Exception):
                raise result_or_exception
            return result_or_exception
        bulk.fetch_ia_files = fake

    def usable_files(self):
        return {
            "a.txt": {"path": self.upload_dir / "a.txt", "size": 5},
            "b.txt": {"path": self.upload_dir / "b.txt", "size": 4},
        }


class TestSyncUploadLog(OfflineTestCase):
    def test_success_imports_remote_files(self):
        self.stub_remote({
            "a.txt": {"size": 5, "md5": md5_of("alpha")},
            "remote-only.txt": {"size": 10, "md5": "deadbeef"},
        })
        result, output = self.run_capture(bulk.sync_upload_log, "test-item", {})

        self.assertTrue(result.remote.available)
        self.assertEqual(result.remote.files["a.txt"]["size"], 5)
        self.assertIn("a.txt", result.upload_log)
        self.assertIn("remote-only.txt", result.upload_log)
        self.assertTrue(result.upload_log["a.txt"]["uploaded"])
        self.assertIn("Synced 2 files from IA", output)

    def test_failure_preserves_local_log_and_marks_remote_unavailable(self):
        self.seed_log("test-item", {
            "a.txt": (5, True, md5_of("alpha")),
            "b.txt": (4, True, md5_of("beta")),
        })
        self.stub_remote(RuntimeError("connection reset"))
        result, output = self.run_capture(
            bulk.sync_upload_log, "test-item", bulk.load_upload_log("test-item"))

        self.assertFalse(result.remote.available)
        self.assertIn("connection reset", result.remote.error)
        # The local log is neither cleared nor treated as empty.
        self.assertEqual(set(result.upload_log), {"a.txt", "b.txt"})
        self.assertTrue(result.upload_log["a.txt"]["uploaded"])
        self.assertIn("Remote file list unavailable", output)

    def test_partial_listing_only_reports_what_it_returned(self):
        self.stub_remote({
            "a.txt": {"size": 5, "md5": md5_of("alpha")},
        })
        result, _ = self.run_capture(bulk.sync_upload_log, "test-item", {})

        self.assertTrue(result.remote.available)
        self.assertIn("a.txt", result.upload_log)
        self.assertNotIn("b.txt", result.upload_log)


class TestUploadPlanning(OfflineTestCase):
    def test_remote_available_skips_all_logged_files_without_unconfirmed(self):
        local = {
            "a.txt": {"path": self.upload_dir / "a.txt", "size": 5},
            "b.txt": {"path": self.upload_dir / "b.txt", "size": 4},
        }
        log = {
            "a.txt": {"size": 5, "uploaded": True, "md5_hash": md5_of("alpha")},
            "b.txt": {"size": 4, "uploaded": True, "md5_hash": md5_of("beta")},
        }
        plan = bulk.plan_upload(local, log, remote_available=True)

        self.assertEqual(plan.to_upload, [])
        self.assertEqual(set(plan.skipped), {"a.txt", "b.txt"})
        self.assertEqual(plan.unconfirmed_skips, [])

    def test_remote_unavailable_skips_logged_files_but_flags_them(self):
        local = {
            "a.txt": {"path": self.upload_dir / "a.txt", "size": 5},
            "b.txt": {"path": self.upload_dir / "b.txt", "size": 4},
        }
        log = {
            "a.txt": {"size": 5, "uploaded": True, "md5_hash": md5_of("alpha")},
        }
        plan = bulk.plan_upload(local, log, remote_available=False)

        # Logged and same size: skip, but recorded as unconfirmed.
        self.assertIn("a.txt", plan.skipped)
        self.assertEqual(plan.unconfirmed_skips, ["a.txt"])
        # No local record: still uploaded.
        self.assertEqual([f["relative_path"] for f in plan.to_upload], ["b.txt"])

    def test_remote_unavailable_still_uploads_size_change_and_failed_entries(self):
        local = {
            "a.txt": {"path": self.upload_dir / "a.txt", "size": 5},
            "b.txt": {"path": self.upload_dir / "b.txt", "size": 4},
        }
        log = {
            "a.txt": {"size": 99, "uploaded": True, "md5_hash": "x"},
            "b.txt": {"size": 4, "uploaded": False, "md5_hash": None},
        }
        plan = bulk.plan_upload(local, log, remote_available=False)

        self.assertEqual({f["relative_path"] for f in plan.to_upload}, {"a.txt", "b.txt"})
        self.assertEqual(plan.unconfirmed_skips, [])

    def test_partial_remote_listing_still_uploads_unknown_files(self):
        local = {
            "a.txt": {"path": self.upload_dir / "a.txt", "size": 5},
            "b.txt": {"path": self.upload_dir / "b.txt", "size": 4},
        }
        log = {
            "a.txt": {"size": 5, "uploaded": True, "md5_hash": md5_of("alpha")},
        }
        plan = bulk.plan_upload(local, log, remote_available=True)

        self.assertEqual(plan.skipped, ["a.txt"])
        self.assertEqual([f["relative_path"] for f in plan.to_upload], ["b.txt"])
        self.assertEqual(plan.unconfirmed_skips, [])


class TestVerification(OfflineTestCase):
    def test_remote_unavailable_marks_everything_unconfirmed_not_failed(self):
        local = self.usable_files()
        log = {name: {"size": info["size"], "uploaded": True, "md5_hash": None}
               for name, info in local.items()}
        remote = bulk.RemoteFileList({}, False, "HTTP 503")

        result, output = self.run_capture(
            bulk.compare_files_with_remote, local, log, remote, identifier="test-item")

        self.assertEqual(result.matched, [])
        self.assertEqual(result.mismatched, [])
        self.assertEqual(set(result.unconfirmed), {"a.txt", "b.txt"})
        self.assertTrue(result.ok)
        self.assertIn("UNCONFIRMED", output)

    def test_remote_unavailable_end_to_end_exits_zero_with_warning(self):
        self.stub_remote(ConnectionError("IA unreachable"))
        local = self.usable_files()
        log = {name: {"size": info["size"], "uploaded": True, "md5_hash": None}
               for name, info in local.items()}

        result, output = self.run_capture(
            bulk.verify_upload, "test-item", local, log,
            wait_timeout=0, interval=0)
        outcome = bulk.as_process_outcome(result)

        self.assertEqual(set(result.unconfirmed), {"a.txt", "b.txt"})
        self.assertEqual(result.mismatched, [])
        self.assertTrue(outcome.exit_ok)
        self.assertEqual(outcome.unconfirmed, 2)
        self.assertIn("could NOT be verified", output)
        self.assertIn("re-run when IA is reachable", output)

    def test_remote_available_classifies_match_mismatch_missing(self):
        (self.upload_dir / "c.bin").write_bytes(b"gamma")
        local = {
            "a.txt": {"path": self.upload_dir / "a.txt", "size": 5},
            "b.txt": {"path": self.upload_dir / "b.txt", "size": 4},
            "c.bin": {"path": self.upload_dir / "c.bin", "size": 5},
        }
        remote = bulk.RemoteFileList({
            # a.txt matches exactly
            "a.txt": {"size": 5, "md5": md5_of("alpha")},
            # b.txt has a different hash -> confirmed mismatch
            "b.txt": {"size": 4, "md5": md5_of("BETA")},
            # c.bin is absent from a readable list -> confirmed missing
        }, True)

        result, output = self.run_capture(
            bulk.compare_files_with_remote, local, {}, remote)

        self.assertEqual(result.matched, ["a.txt"])
        self.assertEqual(set(result.mismatched), {"b.txt", "c.bin"})
        self.assertEqual(result.unconfirmed, [])
        self.assertFalse(result.ok)
        self.assertIn("MISMATCH", output)
        self.assertIn("MISSING", output)

    def test_partial_remote_listing_is_a_confirmed_problem(self):
        self.stub_remote({"a.txt": {"size": 5, "md5": md5_of("alpha")}})
        local = self.usable_files()

        result, _ = self.run_capture(
            bulk.verify_upload, "test-item", local, {},
            wait_timeout=0, interval=0)
        outcome = bulk.as_process_outcome(result)

        self.assertEqual(result.matched, ["a.txt"])
        self.assertEqual(result.mismatched, ["b.txt"])  # readable list lacks b.txt
        self.assertEqual(result.unconfirmed, [])
        self.assertFalse(outcome.exit_ok)

    def test_remote_readable_but_mixed_with_unverified_has_no_unconfirmed(self):
        self.stub_remote({
            "a.txt": {"size": 5, "md5": md5_of("alpha")},
            "b.txt": {"size": 4, "md5": md5_of("beta")},
        })
        local = self.usable_files()

        result, _ = self.run_capture(
            bulk.verify_upload, "test-item", local, {},
            wait_timeout=0, interval=0)

        self.assertEqual(set(result.matched), {"a.txt", "b.txt"})
        self.assertEqual(result.unconfirmed, [])
        self.assertTrue(bulk.as_process_outcome(result).exit_ok)


    def test_unreadable_verify_keeps_uploaded_flag_for_next_run(self):
        """A verify pass must not downgrade a just-uploaded file to 'failed'."""
        self.stub_remote(RuntimeError("IA down"))
        local = self.usable_files()

        # Caller passes the pre-upload log (no entries at all, as after a
        # force upload); the upload itself already wrote 'uploaded' rows.
        self.seed_log("test-item", {
            "a.txt": (5, True, md5_of("alpha")),
            "b.txt": (4, True, md5_of("beta")),
        })
        self.run_capture(bulk.verify_upload, "test-item", local, {},
                         wait_timeout=0, interval=0)

        log = bulk.load_upload_log("test-item")
        self.assertTrue(log["a.txt"]["uploaded"])
        self.assertTrue(log["b.txt"]["uploaded"])


class TestProcessUploadResume(OfflineTestCase):
    """End-to-end decision path with a stubbed remote and a stubbed item."""

    def exercise(self, remote):
        self.stub_remote(remote)
        # Avoid touching the real IA item during upload.
        uploaded = []

        class FakeResponse:
            status_code = 200
            text = ""

        class FakeItem:
            def upload(self, files=None, **kwargs):
                for name, fileobj in (files or {}).items():
                    uploaded.append(name)
                    if hasattr(fileobj, "read"):
                        fileobj.read()
                    if hasattr(fileobj, "close"):
                        fileobj.close()
                return [FakeResponse()]

        real_verify = bulk.verify_upload

        def fast_verify(*args, **kwargs):
            kwargs.setdefault("wait_timeout", 0)
            kwargs.setdefault("interval", 0)
            return real_verify(*args, **kwargs)

        bulk.get_item = lambda identifier: FakeItem()
        bulk.verify_upload = fast_verify
        try:
            outcome, output = self.run_capture(
                bulk.process_upload, "test-item", str(self.upload_dir),
                force_upload=False, metadata=None)
        finally:
            bulk.get_item = self._saved["get_item"]
            bulk.verify_upload = real_verify
        return outcome, uploaded, output

    def test_unreadable_remote_reuses_local_log_and_uploads_unknown(self):
        self.seed_log("test-item", {"a.txt": (5, True, md5_of("alpha"))})

        outcome, uploaded, output = self.exercise(RuntimeError("IA down"))

        # a.txt is skipped on local evidence only; b.txt is uploaded.
        self.assertEqual(uploaded, ["b.txt"])
        self.assertEqual(outcome.unconfirmed, 2)  # a + b; b was re-checked at verify
        self.assertTrue(outcome.exit_ok)
        self.assertIn("skipped based on the local upload log only", output)
        self.assertIn("UNCONFIRMED", output)
        # b.txt's upload was recorded, a.txt's original entry survived.
        log = bulk.load_upload_log("test-item")
        self.assertEqual(set(log), {"a.txt", "b.txt"})

    def test_unreadable_remote_does_not_reupload_logged_file(self):
        self.seed_log("test-item", {
            "a.txt": (5, True, md5_of("alpha")),
            "b.txt": (4, True, md5_of("beta")),
        })

        outcome, uploaded, output = self.exercise(RuntimeError("IA down"))

        self.assertEqual(uploaded, [])
        self.assertTrue(outcome.exit_ok)
        self.assertIn("All files are already uploaded", output)
        self.assertIn("📤 To upload:       0", output)
        self.assertIn("Skipped (local log, IA unconfirmed): 2", output)

    def test_readable_remote_matches_existing_behaviour(self):
        # Remote lists a.txt only; b.txt has no local record -> upload it.
        self.stub_remote({
            "a.txt": {"size": 5, "md5": md5_of("alpha")},
        })
        outcome, uploaded, output = self.exercise({
            "a.txt": {"size": 5, "md5": md5_of("alpha")},
        })

        self.assertEqual(uploaded, ["b.txt"])
        # a.txt verified OK from the stub; b.txt was uploaded but the stub
        # still omits it from the readable list -> confirmed missing.
        self.assertEqual(outcome.unconfirmed, 0)
        self.assertFalse(outcome.exit_ok)


class TestExitCode(unittest.TestCase):
    """main() must exit 0 for unconfirmed-only runs, non-zero for mismatches."""

    def run_main(self, outcome):
        saved = {"_ARGS": bulk._ARGS, "process_upload": bulk.process_upload,
                 "ASSUME_YES": bulk.ASSUME_YES}
        tmpdir = tempfile.mkdtemp()
        bulk.ASSUME_YES = True
        bulk._ARGS = ["test-item", tmpdir]
        bulk.process_upload = lambda *args, **kwargs: outcome
        try:
            with redirect_stdout(io.StringIO()):
                bulk.main()
        except SystemExit as exc:
            return exc.code
        finally:
            for name, value in saved.items():
                setattr(bulk, name, value)
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        return None

    def test_unconfirmed_only_exits_zero(self):
        code = self.run_main(bulk.ProcessOutcome(True, unconfirmed=3))
        self.assertEqual(code, 0)

    def test_confirmed_mismatch_exits_non_zero(self):
        code = self.run_main(bulk.ProcessOutcome(False, unconfirmed=1))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
