import os
import tempfile
import unittest
from types import SimpleNamespace

from telethon.tl.types import MessageMediaPhoto

from main import (
    DownloadSummary,
    assign_unique_file_names,
    cleanup_incomplete_files,
    download_file,
    download_files_from_entity,
    sanitize_filename,
)


class FakeFile:
    """Stand-in for Telethon's DocumentAttributeFilename container."""

    def __init__(self, name, mime_type):
        self.name = name
        self.mime_type = mime_type


class FakeMessage:
    """Minimal stand-in for a Telethon message with optional media."""

    def __init__(self, message_id, name=None, mime_type=None, photo=False):
        self.id = message_id
        if photo:
            self.media = MessageMediaPhoto()
            self.file = FakeFile(name, mime_type or "image/jpeg")
        elif name is not None or mime_type is not None:
            self.media = SimpleNamespace(document=message_id)
            self.file = FakeFile(name, mime_type)
        else:
            self.media = None
            self.file = None


def text_message(message_id):
    """A message without media, which must be ignored by the download loop."""
    message = FakeMessage(message_id)
    return message


class FakeClient:
    """Deterministic stand-in for TelegramClient used by the download loop.

    ``behaviour`` maps a message ID to one of:
      * ``"ok"`` (default) - write ``payload`` bytes to the requested path;
      * ``"raise"`` - raise ``error`` instead of writing;
      * ``"empty"`` - create a zero-byte file;
      * ``"noop"`` - return a path without writing anything.
    """

    def __init__(self, messages, behaviour=None, payload=b"data", error=None):
        self.messages = messages
        self.behaviour = behaviour or {}
        self.payload = payload
        self.error = error or RuntimeError("simulated network failure")
        self.requested_limits = []
        self.downloaded_ids = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def iter_messages(self, entity, limit=None):
        self.requested_limits.append(limit)
        messages = self.messages if limit in (None, 0) else self.messages[:limit]
        yield from messages

    def download_media(self, message, file=None):
        self.downloaded_ids.append(message.id)
        action = self.behaviour.get(message.id, "ok")
        if action == "raise":
            raise self.error
        if action == "empty":
            with open(file, "wb"):
                pass
            return file
        if action == "noop":
            return file
        with open(file, "wb") as handle:
            handle.write(self.payload)
        return file


class DownloadTestBase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.output = self._directory.name

    def tearDown(self):
        self._directory.cleanup()

    def run_download(self, messages, **kwargs):
        client = kwargs.pop("client", None) or FakeClient(messages)
        return (
            download_files_from_entity(
                client, "test_channel", save_directory=self.output, **kwargs
            ),
            client,
        )

    def path(self, name):
        return os.path.join(self.output, name)

    def files(self):
        return sorted(os.listdir(self.output))


class TestSanitizeFilename(unittest.TestCase):
    def test_replaces_invalid_os_characters(self):
        dangerous_name = 'What: is this? / A "test" * file <1> | part 2.txt'
        cleaned = sanitize_filename(dangerous_name, fallback="101")
        for bad_char in '<>:"/\\|?*':
            self.assertNotIn(bad_char, cleaned)
        self.assertTrue(cleaned.endswith(".txt"))

    def test_strips_trailing_dots_and_spaces(self):
        self.assertEqual(
            sanitize_filename("my_document. ", fallback="102"), "my_document"
        )

    def test_neutralizes_path_traversal_inputs(self):
        cleaned = sanitize_filename("../../etc/passwd", fallback="103")
        self.assertNotIn("/", cleaned)
        self.assertNotIn("\\", cleaned)
        self.assertEqual(sanitize_filename("..", fallback="104"), "104")
        self.assertEqual(sanitize_filename(".", fallback="105"), "105")

    def test_empty_or_symbol_only_names_fall_back_to_message_id(self):
        # Empty cleaned filenames must not collide on a fixed placeholder.
        self.assertEqual(sanitize_filename("", fallback="4201"), "4201")
        self.assertEqual(sanitize_filename("???:::", fallback="4202"), "4202")
        self.assertNotEqual(
            sanitize_filename("???", fallback="4201"),
            sanitize_filename("???", fallback="4202"),
        )


class TestCleanupIncompleteFiles(DownloadTestBase):
    def test_removes_only_tmp_files_and_reports_them(self):
        with open(self.path("keep.txt"), "wb") as handle:
            handle.write(b"ok")
        with open(self.path("stale.pdf.tmp"), "wb") as handle:
            handle.write(b"partial")
        with open(self.path("nested.tmp"), "wb") as handle:
            handle.write(b"partial")

        removed = cleanup_incomplete_files(self.output)

        self.assertEqual(removed, [self.path("nested.tmp"), self.path("stale.pdf.tmp")])
        self.assertEqual(self.files(), ["keep.txt"])

    def test_interrupted_download_is_cleaned_and_replaced(self):
        with open(self.path("report.pdf.tmp"), "wb") as handle:
            handle.write(b"half-written")

        summary, _ = self.run_download(
            [FakeMessage(7, name="report.pdf", mime_type="application/pdf")]
        )

        self.assertEqual(summary.removed_temp_files, [self.path("report.pdf.tmp")])
        self.assertEqual(summary.new_count, 1)
        self.assertEqual(self.files(), ["report.pdf"])


class TestDownloadFile(DownloadTestBase):
    def test_new_download_returns_new_status(self):
        client = FakeClient([])
        outcome = download_file(client, FakeMessage(1), self.path("a.bin"))
        self.assertEqual(outcome.status, "new")
        self.assertEqual(outcome.file_path, self.path("a.bin"))
        self.assertEqual(outcome.size, len(b"data"))
        self.assertEqual(self.files(), ["a.bin"])

    def test_existing_file_is_skipped_and_untouched(self):
        with open(self.path("a.bin"), "wb") as handle:
            handle.write(b"existing-content")
        client = FakeClient([])

        outcome = download_file(client, FakeMessage(1), self.path("a.bin"))

        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.size, len(b"existing-content"))
        self.assertEqual(client.downloaded_ids, [])
        with open(self.path("a.bin"), "rb") as handle:
            self.assertEqual(handle.read(), b"existing-content")

    def test_exception_becomes_failure_with_reason_and_no_temp_left(self):
        client = FakeClient([], behaviour={5: "raise"}, error=RuntimeError("boom"))
        outcome = download_file(client, FakeMessage(5), self.path("a.bin"))

        self.assertEqual(outcome.status, "failed")
        self.assertIn("RuntimeError", outcome.reason)
        self.assertIn("boom", outcome.reason)
        self.assertEqual(self.files(), [])

    def test_empty_file_is_a_failure(self):
        client = FakeClient([], behaviour={5: "empty"})
        outcome = download_file(client, FakeMessage(5), self.path("a.bin"))

        self.assertEqual(outcome.status, "failed")
        self.assertIn("empty", outcome.reason)
        self.assertEqual(self.files(), [])

    def test_missing_output_is_a_failure(self):
        client = FakeClient([], behaviour={5: "noop"})
        outcome = download_file(client, FakeMessage(5), self.path("a.bin"))

        self.assertEqual(outcome.status, "failed")
        self.assertIn("no file", outcome.reason)
        self.assertEqual(self.files(), [])


class TestCollisionHandling(unittest.TestCase):
    def names(self, messages):
        candidates = [(message, message.id, name) for message, name in messages]
        return [name for _, _, name in assign_unique_file_names(candidates)]

    def test_duplicate_names_get_message_ids_for_every_member(self):
        messages = [
            (FakeMessage(101), "report.pdf"),
            (FakeMessage(102), "report.pdf"),
        ]
        self.assertEqual(self.names(messages), ["report (101).pdf", "report (102).pdf"])

    def test_unique_names_are_left_alone(self):
        messages = [(FakeMessage(1), "a.pdf"), (FakeMessage(2), "b.pdf")]
        self.assertEqual(self.names(messages), ["a.pdf", "b.pdf"])

    def test_disambiguation_is_case_insensitive(self):
        # Windows and macOS treat these as the same file.
        messages = [(FakeMessage(1), "Data.CSV"), (FakeMessage(2), "data.csv")]
        self.assertEqual(self.names(messages), ["Data (1).CSV", "data (2).csv"])

    def test_suffix_collision_with_existing_base_name_is_resolved(self):
        messages = [
            (FakeMessage(101), "report.pdf"),
            (FakeMessage(102), "report.pdf"),
            (FakeMessage(103), "report (101).pdf"),
        ]
        names = self.names(messages)
        self.assertEqual(
            names,
            ["report (101-2).pdf", "report (102).pdf", "report (101).pdf"],
        )
        self.assertEqual(len({name.casefold() for name in names}), 3)

    def test_repeated_runs_produce_identical_names(self):
        def build():
            return [
                (FakeMessage(11), "clash.txt"),
                (FakeMessage(12), "clash.txt"),
                (FakeMessage(13), "other.txt"),
            ]

        self.assertEqual(self.names(build()), self.names(build()))


class TestDownloadFilesFromEntity(DownloadTestBase):
    def test_mixed_batch_reports_new_skipped_and_failed(self):
        existing = self.path("existing.pdf")
        with open(existing, "wb") as handle:
            handle.write(b"0123456789")

        messages = [
            FakeMessage(1, name="fresh.pdf", mime_type="application/pdf"),
            FakeMessage(2, name="existing.pdf", mime_type="application/pdf"),
            FakeMessage(3, name="broken.pdf", mime_type="application/pdf"),
            text_message(4),
        ]
        client = FakeClient(
            messages,
            behaviour={3: "raise"},
            payload=b"12345",
        )

        summary = download_files_from_entity(
            client, "test_channel", save_directory=self.output
        )

        self.assertIsInstance(summary, DownloadSummary)
        self.assertEqual([file.message_id for file in summary.new_files], [1])
        self.assertEqual([file.message_id for file in summary.skipped_files], [2])
        self.assertEqual([file.message_id for file in summary.failed_files], [3])
        self.assertEqual(summary.new_count, 1)
        self.assertEqual(summary.skipped_count, 1)
        self.assertEqual(summary.failed_count, 1)
        self.assertIn("simulated network failure", summary.failed_files[0].reason)
        self.assertEqual(summary.new_size_bytes, 5)
        self.assertEqual(summary.skipped_size_bytes, 10)
        self.assertEqual(summary.total_size_bytes, 15)
        self.assertEqual(self.files(), ["existing.pdf", "fresh.pdf"])

    def test_logged_summary_states_counts_and_total_size(self):
        messages = [
            FakeMessage(1, name="fresh.pdf", mime_type="application/pdf"),
            FakeMessage(2, name="broken.pdf", mime_type="application/pdf"),
        ]
        client = FakeClient(messages, behaviour={2: "raise"}, payload=b"x" * 1024)

        with self.assertLogs("main", level="INFO") as captured:
            download_files_from_entity(
                client, "test_channel", save_directory=self.output
            )

        output = "\n".join(captured.output)
        self.assertIn(
            "Summary: new downloads: 1 (0.00 MB), skipped (already existed): 0 "
            "(0.00 MB), failed: 1, total size: 0.00 MB",
            output,
        )
        self.assertIn("message 2", output)
        self.assertIn("simulated network failure", output)

    def test_failure_leaves_no_temp_file(self):
        client = FakeClient(
            [FakeMessage(9, name="broken.pdf", mime_type="application/pdf")],
            behaviour={9: "raise"},
        )
        summary = download_files_from_entity(
            client, "test_channel", save_directory=self.output
        )
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(self.files(), [])

    def test_same_name_messages_do_not_overwrite_each_other(self):
        messages = [
            FakeMessage(101, name="report.pdf", mime_type="application/pdf"),
            FakeMessage(102, name="report.pdf", mime_type="application/pdf"),
        ]
        summary, _ = self.run_download(messages)

        self.assertEqual(summary.new_count, 2)
        self.assertEqual(self.files(), ["report (101).pdf", "report (102).pdf"])

    def test_rerun_skips_both_collision_partners(self):
        messages = [
            FakeMessage(101, name="report.pdf", mime_type="application/pdf"),
            FakeMessage(102, name="report.pdf", mime_type="application/pdf"),
        ]
        self.run_download(messages)
        summary, client = self.run_download(messages)

        self.assertEqual(summary.new_count, 0)
        self.assertEqual(summary.skipped_count, 2)
        self.assertEqual(client.downloaded_ids, [])
        self.assertEqual(self.files(), ["report (101).pdf", "report (102).pdf"])

    def test_collision_partner_does_not_touch_unrelated_completed_file(self):
        # "report.pdf" already exists from an earlier run (different batch);
        # a new pair of colliding messages must not clobber or skip it.
        with open(self.path("report.pdf"), "wb") as handle:
            handle.write(b"older-file")

        messages = [
            FakeMessage(201, name="report.pdf", mime_type="application/pdf"),
            FakeMessage(202, name="report.pdf", mime_type="application/pdf"),
        ]
        summary, _ = self.run_download(messages)

        self.assertEqual(summary.new_count, 2)
        with open(self.path("report.pdf"), "rb") as handle:
            self.assertEqual(handle.read(), b"older-file")
        self.assertEqual(
            self.files(), ["report (201).pdf", "report (202).pdf", "report.pdf"]
        )

    def test_limit_counts_messages_not_downloads(self):
        messages = [
            text_message(1),
            FakeMessage(2, name="one.pdf", mime_type="application/pdf"),
            FakeMessage(3, name="two.pdf", mime_type="application/pdf"),
        ]
        summary, client = self.run_download(messages, message_limit=2)

        self.assertEqual(client.requested_limits, [2])
        self.assertEqual(summary.new_count, 1)
        self.assertEqual(self.files(), ["one.pdf"])

    def test_zero_limit_requests_unlimited_messages(self):
        messages = [FakeMessage(1, name="one.pdf", mime_type="application/pdf")]
        _, client = self.run_download(messages, message_limit=0)
        self.assertEqual(client.requested_limits, [None])

    def test_photos_are_named_after_message_id(self):
        messages = [FakeMessage(55, photo=True)]
        summary, _ = self.run_download(messages)
        self.assertEqual(summary.new_count, 1)
        self.assertEqual(self.files(), ["55.jpg"])

    def test_format_filtering_still_applies(self):
        messages = [
            FakeMessage(1, name="one.pdf", mime_type="application/pdf"),
            FakeMessage(2, name="two.png", mime_type="image/png"),
        ]
        summary, _ = self.run_download(messages, file_type="pdf")
        self.assertEqual([file.message_id for file in summary.new_files], [1])
        self.assertEqual(self.files(), ["one.pdf"])

    def test_path_traversal_names_stay_inside_output(self):
        messages = [
            FakeMessage(1, name="../../escape.txt", mime_type="text/plain"),
            FakeMessage(2, name="..", mime_type="text/plain"),
        ]
        summary, _ = self.run_download(messages)

        self.assertEqual(summary.new_count, 2)
        self.assertEqual(self.files(), [".._.._escape.txt", "2.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.output, "..", "escape.txt")))

    def test_summary_sizes_are_reported_in_mb(self):
        payload = b"x" * (512 * 1024)
        messages = [
            FakeMessage(1, name="half.bin", mime_type="application/octet-stream")
        ]
        summary, _ = self.run_download(
            messages, client=FakeClient(messages, payload=payload)
        )
        self.assertAlmostEqual(summary.new_size_mb, 0.5)
        self.assertAlmostEqual(summary.total_size_mb, 0.5)

    def test_empty_name_falls_back_to_message_id(self):
        messages = [FakeMessage(77, name="???", mime_type="text/plain")]
        summary, _ = self.run_download(messages)
        self.assertEqual(summary.new_count, 1)
        self.assertEqual(self.files(), ["77.txt"])


if __name__ == "__main__":
    unittest.main()
