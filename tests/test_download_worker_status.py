"""
Tests for DownloadWorker chapter-status reporting (GUI side).

The downloader, config and converters are replaced with local fakes, so no
browser, network or Qt event loop is required. ``DownloadWorker.run`` is
invoked directly; PyQt signals connected with ``connect`` are delivered
synchronously in this setup.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication  # noqa: E402

from src.scraper.api_models import Book, Series  # noqa: E402
import gui.backend.download_worker as download_worker_module  # noqa: E402
from gui.backend.download_worker import DownloadWorker  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """QThread signal machinery needs a QCoreApplication instance."""
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def make_config(**overrides):
    values = {
        "download_directory": "unused-downloads",
        "max_concurrent_images": 2,
        "max_retries": 1,
        "download_format": "images",
        "keep_images": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeDownloader:
    """Local stand-in for APIChapterDownloader."""

    # book_id -> (success_pages, total_pages)
    scenario: dict[str, tuple[int, int]] = {}
    # book ids whose page-URL lookup raises
    broken_books: set[str] = set()
    # (book_id, output_dir, failed_pages) for every download call
    calls: list[tuple[str, Path, list[int]]] = []
    instances: list["FakeDownloader"] = []

    def __init__(self, download_dir, max_concurrent_images=10, max_retries=3):
        self.download_dir = download_dir
        self.last_failed_pages: list[int] = []
        self._current_book_id = ""
        FakeDownloader.instances.append(self)

    @staticmethod
    def sanitize_filename(name: str, max_length: int = 80) -> str:
        return name.replace("/", "_")[:max_length]

    def get_page_urls(self, book_id: str) -> list[str]:
        if book_id in FakeDownloader.broken_books:
            raise RuntimeError("manifest unavailable")
        _success, total = FakeDownloader.scenario.get(book_id, (0, 0))
        self._current_book_id = book_id
        if total <= 0:
            return []
        return [f"https://example.test/{book_id}/{n}.webp" for n in range(1, total + 1)]

    def download_from_urls(
        self,
        image_urls,
        output_dir,
        progress_callback=None,
        failed_pages_callback=None,
    ) -> int:
        book_id = self._current_book_id
        success, total = FakeDownloader.scenario.get(book_id, (0, len(image_urls)))
        failed = list(range(success + 1, total + 1))

        FakeDownloader.calls.append((book_id, Path(output_dir), failed))
        self.last_failed_pages = failed
        if progress_callback:
            progress_callback(success, total)
        if failed_pages_callback:
            failed_pages_callback(list(failed))
        return success

    def close(self):
        pass


def make_series() -> Series:
    return Series(series_id="series-1", title="Sample Series")


def make_book(book_id: str, chapter_no: str) -> Book:
    return Book(book_id=book_id, chapter_no=chapter_no, title=f"Title {chapter_no}", page_count=5)


@pytest.fixture(autouse=True)
def patch_worker_deps(monkeypatch, tmp_path):
    """Replace config and downloader for every test and isolate the CWD."""
    monkeypatch.chdir(tmp_path)
    FakeDownloader.scenario = {}
    FakeDownloader.broken_books = set()
    FakeDownloader.calls = []
    FakeDownloader.instances = []

    monkeypatch.setattr(download_worker_module, "APIChapterDownloader", FakeDownloader)
    monkeypatch.setattr(download_worker_module, "get_config", lambda: make_config())
    return tmp_path


def run_worker(chapters):
    worker = DownloadWorker(make_series(), chapters)
    statuses: list[tuple[str, str]] = []
    messages: list[tuple[float, int, str]] = []
    finished: list[tuple[int, int]] = []
    errors: list[str] = []

    worker.chapterComplete.connect(lambda number, status: statuses.append((number, status)))
    worker.progress.connect(lambda current, total, message: messages.append((current, total, message)))
    worker.finished.connect(lambda success, total: finished.append((success, total)))
    worker.error.connect(errors.append)

    worker.run()  # sequential run; no event loop required

    return worker, statuses, messages, finished, errors


def test_complete_chapter_reports_complete():
    FakeDownloader.scenario["book-1"] = (5, 5)

    _worker, statuses, messages, finished, errors = run_worker([make_book("book-1", "1")])

    assert statuses == [("1", "complete")]
    assert finished == [(1, 1)]
    assert errors == []
    # Progress text always reports the failed-page count
    assert any("0 failed" in message for _current, _total, message in messages)


def test_partial_chapter_reports_partial_and_failed_count():
    FakeDownloader.scenario["book-1"] = (3, 5)

    _worker, statuses, messages, finished, errors = run_worker([make_book("book-1", "1")])

    assert statuses == [("1", "partial")]
    assert finished == [(1, 1)]
    assert errors == []
    partial_messages = [m for _c, _t, m in messages if "partially downloaded" in m]
    assert partial_messages, messages
    assert "2 failed" in partial_messages[-1]


def test_failed_chapter_reports_failed():
    FakeDownloader.scenario["book-1"] = (0, 5)

    _worker, statuses, messages, finished, errors = run_worker([make_book("book-1", "1")])

    assert statuses == [("1", "failed")]
    assert finished == [(0, 1)]
    assert errors == []
    assert any("5 failed" in message for _current, _total, message in messages)


def test_mixed_chapters_report_each_status():
    FakeDownloader.scenario["book-1"] = (5, 5)
    FakeDownloader.scenario["book-2"] = (2, 5)
    FakeDownloader.scenario["book-3"] = (0, 5)

    _worker, statuses, _messages, finished, errors = run_worker([
        make_book("book-1", "1"),
        make_book("book-2", "2"),
        make_book("book-3", "3"),
    ])

    assert statuses == [("1", "complete"), ("2", "partial"), ("3", "failed")]
    assert finished == [(2, 3)]  # complete + partial chapters count as successes
    assert errors == []


def test_missing_manifest_marks_chapter_failed():
    FakeDownloader.broken_books = {"book-1"}

    _worker, statuses, _messages, finished, errors = run_worker([make_book("book-1", "1")])

    assert statuses == [("1", "failed")]
    assert finished == [(0, 1)]


def test_conversion_runs_for_complete_and_partial_but_not_failed(monkeypatch):
    FakeDownloader.scenario["book-1"] = (5, 5)
    FakeDownloader.scenario["book-2"] = (2, 5)
    FakeDownloader.scenario["book-3"] = (0, 5)

    monkeypatch.setattr(
        download_worker_module, "get_config", lambda: make_config(download_format="pdf")
    )
    converted: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        download_worker_module,
        "create_pdf",
        lambda chapter_dir, delete_images=False: converted.append(
            (Path(chapter_dir).name, delete_images)
        ),
    )

    run_worker([
        make_book("book-1", "1"),
        make_book("book-2", "2"),
        make_book("book-3", "3"),
    ])

    names = [name for name, _delete in converted]
    assert len(converted) == 2
    assert any("Chapter_1_" in name for name in names)
    assert any("Chapter_2_" in name for name in names)
    assert not any("Chapter_3_" in name for name in names)


def test_partial_chapter_keeps_images_for_resume(monkeypatch):
    """delete_images must not wipe a partial chapter's resumable pages."""
    FakeDownloader.scenario["book-2"] = (2, 5)

    monkeypatch.setattr(
        download_worker_module, "get_config", lambda: make_config(download_format="pdf")
    )
    converted: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        download_worker_module,
        "create_pdf",
        lambda chapter_dir, delete_images=False: converted.append(
            (Path(chapter_dir).name, delete_images)
        ),
    )

    run_worker([make_book("book-2", "2")])

    assert len(converted) == 1
    assert converted[0][1] is False


def test_partial_chapter_records_failed_pages_from_callback():
    FakeDownloader.scenario["book-2"] = (3, 5)

    _worker, _statuses, _messages, _finished, _errors = run_worker([make_book("book-2", "2")])

    assert FakeDownloader.calls, "downloader was called"
    _book_id, _dir, failed = FakeDownloader.calls[0]
    assert failed == [4, 5]
