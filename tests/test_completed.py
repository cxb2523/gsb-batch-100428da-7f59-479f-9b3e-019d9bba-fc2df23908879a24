"""Tests for completion records, concurrent appends, and rendezvous logic."""

import multiprocessing as mp
import threading
import json
from pathlib import Path

import pytest

from gphotos_export.utils import (
    OtherCompletedIndex,
    append_completed,
    clean_url,
    completed_urls,
    read_completed,
)
from gphotos_export.download import reached_rendezvous


URL_A = "https://photos.google.com/u/0/photo/aaa?pli=1"
URL_A_CLEAN = "https://photos.google.com/photo/aaa"
URL_B = "https://photos.google.com/photo/bbb"
URL_C = "https://photos.google.com/photo/ccc#fragment"


class TestAppendAndRead:
    def test_append_writes_one_jsonl_line_with_url_and_path(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        record = append_completed(record_file, URL_A, tmp_path / "2024" / "1" / "a.jpg")

        raw = record_file.read_text(encoding="utf-8")
        assert raw.endswith("\n")
        assert raw.count("\n") == 1  # exactly one complete line

        parsed = json.loads(raw)
        # URL is cleaned before being stored.
        assert parsed["url"] == URL_A_CLEAN
        assert parsed["path"] == str(tmp_path / "2024" / "1" / "a.jpg")
        assert "ts" in parsed
        assert record["url"] == URL_A_CLEAN

    def test_read_returns_records_keyed_by_cleaned_url(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        append_completed(record_file, URL_A, tmp_path / "a.jpg")
        append_completed(record_file, URL_B, tmp_path / "b.jpg")

        records = read_completed(record_file)
        assert set(records.keys()) == {URL_A_CLEAN, URL_B}
        assert records[URL_A_CLEAN]["path"].endswith("a.jpg")

    def test_completed_urls_helper(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        append_completed(record_file, URL_A, tmp_path / "a.jpg")
        assert completed_urls(record_file) == {URL_A_CLEAN}

    def test_read_missing_file_returns_empty(self, tmp_path):
        assert read_completed(tmp_path / "nope.jsonl") == {}
        assert completed_urls(tmp_path / "nope.jsonl") == set()

    def test_extra_fields_are_persisted(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        append_completed(
            record_file, URL_B, tmp_path / "b.jpg", extra={"phase": "start"},
        )
        records = read_completed(record_file)
        assert records[URL_B]["phase"] == "start"

    def test_multiple_appends_never_interleave_lines(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        for i in range(5):
            append_completed(
                record_file,
                f"https://photos.google.com/photo/id{i}",
                tmp_path / f"f{i}.jpg",
            )
        lines = record_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 5
        # Every line must be a standalone, parseable JSON object.
        for i, line in enumerate(lines):
            obj = json.loads(line)
            assert obj["url"] == f"https://photos.google.com/photo/id{i}"


class TestCorruptLines:
    def test_unparseable_json_line_is_skipped(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        append_completed(record_file, URL_A, tmp_path / "a.jpg")
        with open(record_file, "ab") as fh:
            fh.write(b"{this is not valid json\n")
        append_completed(record_file, URL_B, tmp_path / "b.jpg")

        records = read_completed(record_file)
        assert set(records.keys()) == {URL_A_CLEAN, URL_B}

    def test_truncated_line_is_skipped(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        good = json.dumps(
            {"url": URL_A_CLEAN, "path": str(tmp_path / "a.jpg"), "ts": "t"}
        )
        record_file.write_bytes(
            good.encode() + b"\n"
            b'{"url": "https://photos.google.com/photo/trunc", "path": "C:\\\\pa'
        )
        records = read_completed(record_file)
        assert list(records.keys()) == [URL_A_CLEAN]

    def test_non_object_line_is_skipped(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        record_file.write_text(
            json.dumps({"url": URL_A_CLEAN, "path": "a.jpg"}) + "\n"
            + json.dumps(["just", "a", "list"]) + "\n"
            + "\n"  # blank line tolerated too
        )
        records = read_completed(record_file)
        assert list(records.keys()) == [URL_A_CLEAN]

    def test_record_missing_url_or_path_is_skipped(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        record_file.write_text(
            json.dumps({"url": URL_A_CLEAN, "path": "a.jpg"}) + "\n"
            + json.dumps({"url": URL_B}) + "\n"                # no path
            + json.dumps({"path": "x.jpg"}) + "\n"             # no url
            + json.dumps({"url": "", "path": "y.jpg"}) + "\n"  # empty url
        )
        records = read_completed(record_file)
        assert list(records.keys()) == [URL_A_CLEAN]

    def test_all_lines_corrupt_yields_empty_not_error(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        record_file.write_bytes(b"garbage\n\x00\x01\x02\n")
        assert read_completed(record_file) == {}


def _mp_append_worker(record_file_str, index, count):
    """Top-level worker so Windows 'spawn' can pickle it."""
    record_file = Path(record_file_str)
    for i in range(count):
        append_completed(
            record_file,
            f"https://photos.google.com/u/0/photo/p{index}-{i}?x=1",
            f"downloads/{index}/{i}.jpg",
        )


class TestConcurrentAppend:
    def test_threads_lose_no_records(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        n_threads, per_thread = 8, 25

        threads = [
            threading.Thread(
                target=_mp_append_worker,
                args=(str(record_file), t, per_thread),
            )
            for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * per_thread
        records = read_completed(record_file)
        assert len(records) == expected
        # Every physical line must be intact JSON — no half lines.
        lines = record_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == expected
        for line in lines:
            json.loads(line)

    def test_processes_lose_no_records(self, tmp_path):
        record_file = tmp_path / ".completed.jsonl"
        n_procs, per_proc = 4, 20
        ctx = mp.get_context("spawn")
        procs = [
            ctx.Process(
                target=_mp_append_worker,
                args=(str(record_file), p, per_proc),
            )
            for p in range(n_procs)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=120)
            assert p.exitcode == 0, f"worker exited with {p.exitcode}"

        expected = n_procs * per_proc
        records = read_completed(record_file)
        assert len(records) == expected
        lines = record_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == expected
        for line in lines:
            json.loads(line)
        # Cleaned URLs (no /u/0/, no query) are what got stored.
        assert all("/u/" not in url and "?" not in url for url in records)


class TestOtherCompletedIndex:
    def test_hit_and_miss(self, tmp_path):
        other_file = tmp_path / "backward.completed.jsonl"
        append_completed(other_file, URL_A, tmp_path / "a.jpg")

        index = OtherCompletedIndex(other_file)
        # Hit even when the incoming URL carries a /u/ prefix + query.
        assert index.contains(URL_A) is True
        assert URL_A in index
        assert index.contains(URL_B) is False
        assert index.urls == {URL_A_CLEAN}

    def test_picks_up_appends_after_construction(self, tmp_path):
        other_file = tmp_path / "backward.completed.jsonl"
        index = OtherCompletedIndex(other_file)
        assert index.contains(URL_B) is False

        # Simulate the opposite worker appending after we started.
        append_completed(other_file, URL_B, tmp_path / "b.jpg")
        assert index.contains(URL_B) is True
        assert URL_B in index.urls

    def test_missing_file_is_empty_and_recovers_when_created(self, tmp_path):
        other_file = tmp_path / "not-there-yet.jsonl"
        index = OtherCompletedIndex(other_file)
        assert index.urls == set()
        assert index.contains(URL_A) is False

        append_completed(other_file, URL_A, tmp_path / "a.jpg")
        assert index.contains(URL_A) is True

    def test_corrupt_foreign_file_does_not_break_index(self, tmp_path):
        other_file = tmp_path / "backward.completed.jsonl"
        append_completed(other_file, URL_A, tmp_path / "a.jpg")
        with open(other_file, "ab") as fh:
            fh.write(b"half-written-line")
        index = OtherCompletedIndex(other_file)
        assert index.contains(URL_A) is True


class TestRendezvous:
    def test_hit_despite_redirect_prefix_and_query(self, tmp_path):
        """The original bug: string comparison missed /u/0/ + query URLs."""
        other_file = tmp_path / ".completed.jsonl"
        append_completed(other_file, URL_A_CLEAN, tmp_path / "a.jpg")
        index = OtherCompletedIndex(other_file)

        assert reached_rendezvous(index, "https://photos.google.com/u/0/photo/aaa?pli=1")
        assert reached_rendezvous(index, "https://photos.google.com/photo/aaa#frag")

    def test_miss_keeps_worker_running(self, tmp_path):
        other_file = tmp_path / ".completed.jsonl"
        append_completed(other_file, URL_A, tmp_path / "a.jpg")
        index = OtherCompletedIndex(other_file)

        assert not reached_rendezvous(index, URL_B)
        assert not reached_rendezvous(index, URL_C)

    def test_none_index_never_rendezvous(self):
        assert reached_rendezvous(None, URL_A) is False


class TestCleanUrlNormalization:
    def test_strips_query_and_fragment(self):
        assert clean_url("https://photos.google.com/photo/abc?pli=1&auth=x") == \
            "https://photos.google.com/photo/abc"
        assert clean_url("https://photos.google.com/photo/abc#section") == \
            "https://photos.google.com/photo/abc"

    def test_strips_user_segment_and_query_together(self):
        assert clean_url("https://photos.google.com/u/3/photo/abc?z=1") == \
            "https://photos.google.com/photo/abc"

    def test_strips_trailing_slash(self):
        assert clean_url("https://photos.google.com/photo/abc/") == \
            "https://photos.google.com/photo/abc"
