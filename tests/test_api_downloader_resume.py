"""
Tests for APIChapterDownloader resume and partial-failure settlement.

All network access is replaced by a local fake session, so these tests run
offline and can be reproduced with hand-made sample data.
"""

from pathlib import Path

import pytest

from src.scraper.api_downloader import (
    APIChapterDownloader,
    classify_chapter_status,
    normalize_page_numbers,
    CHAPTER_COMPLETE,
    CHAPTER_FAILED,
    CHAPTER_PARTIAL,
)


class FakeResponse:
    """Minimal stand-in for a curl_cffi streaming response."""

    def __init__(self, body: bytes = b"image-bytes", content_type: str = "image/webp"):
        self._body = body
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size: int = 8192):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start:start + chunk_size]


class FakeSession:
    """Records requested URLs and answers per-URL with success or failure."""

    def __init__(self, failing_urls=(), empty_urls=()):
        self.requested: list[str] = []
        self.failing_urls = set(failing_urls)
        self.empty_urls = set(empty_urls)

    def get(self, url, **kwargs):
        self.requested.append(url)
        if url in self.failing_urls:
            raise RuntimeError(f"forced failure for {url}")
        if url in self.empty_urls:
            return FakeResponse(body=b"")
        return FakeResponse()


def make_downloader(tmp_path: Path, session: FakeSession, max_retries: int = 1) -> APIChapterDownloader:
    downloader = APIChapterDownloader(
        download_dir=tmp_path,
        max_concurrent_images=2,
        max_retries=max_retries,
    )
    downloader.session = session
    return downloader


def page_urls(count: int) -> list[str]:
    return [f"https://example.test/page/{index}.webp" for index in range(1, count + 1)]


def test_resume_skips_non_empty_page_and_counts_it(tmp_path):
    """A non-empty file for page 1 is reused without any request."""
    (tmp_path / "001.webp").write_bytes(b"already-downloaded")
    session = FakeSession()
    downloader = make_downloader(tmp_path, session)
    failed_batches: list[list[int]] = []

    downloaded = downloader.download_from_urls(
        page_urls(3),
        tmp_path,
        failed_pages_callback=failed_batches.append,
    )

    assert downloaded == 3
    assert session.requested == page_urls(3)[1:]  # page 1 never requested
    assert failed_batches == [[]]
    assert downloader.last_failed_pages == []
    assert (tmp_path / "001.webp").read_bytes() == b"already-downloaded"


def test_resume_accepts_any_extension_for_the_same_page_number(tmp_path):
    """Resume matches by page-number stem, not by image extension."""
    (tmp_path / "002.png").write_bytes(b"png-page")
    session = FakeSession()
    downloader = make_downloader(tmp_path, session)

    downloaded = downloader.download_from_urls(page_urls(3), tmp_path)

    assert downloaded == 3
    assert page_urls(3)[1] not in session.requested


def test_zero_byte_sibling_is_removed_when_page_is_resumed(tmp_path):
    """A zero-byte duplicate next to a valid page must not survive the resume."""
    (tmp_path / "001.webp").write_bytes(b"already-downloaded")
    (tmp_path / "001.jpg").write_bytes(b"")
    session = FakeSession()
    downloader = make_downloader(tmp_path, session)

    downloaded = downloader.download_from_urls(page_urls(1), tmp_path)

    assert downloaded == 1
    assert not (tmp_path / "001.jpg").exists()
    assert (tmp_path / "001.webp").exists()


def test_empty_existing_page_is_downloaded(tmp_path):
    """A zero-byte leftover must not be treated as a finished page."""
    (tmp_path / "001.webp").write_bytes(b"")
    session = FakeSession()
    downloader = make_downloader(tmp_path, session)

    downloaded = downloader.download_from_urls(page_urls(2), tmp_path)

    assert downloaded == 2
    assert page_urls(2)[0] in session.requested
    assert (tmp_path / "001.webp").stat().st_size > 0


def test_single_failure_does_not_stop_other_pages(tmp_path):
    """One exhausted page is reported while the rest still download."""
    urls = page_urls(5)
    session = FakeSession(failing_urls={urls[1], urls[3]})
    downloader = make_downloader(tmp_path, session)
    failed_batches: list[list[int]] = []
    progress: list[tuple[int, int]] = []

    downloaded = downloader.download_from_urls(
        urls,
        tmp_path,
        progress_callback=lambda done, total: progress.append((done, total)),
        failed_pages_callback=failed_batches.append,
    )

    assert downloaded == 3
    assert failed_batches == [[2, 4]]
    assert downloader.last_failed_pages == [2, 4]
    assert sorted(p.stem for p in tmp_path.iterdir()) == ["001", "003", "005"]
    # Progress is monotonic and ends at the settled count
    assert progress[-1] == (3, 5)
    assert all(done <= total for done, total in progress)


def test_all_pages_failing_reports_every_page(tmp_path):
    """When every page fails, nothing is counted and all pages are reported."""
    urls = page_urls(3)
    session = FakeSession(failing_urls=set(urls))
    downloader = make_downloader(tmp_path, session)
    failed_batches: list[list[int]] = []

    downloaded = downloader.download_from_urls(
        urls,
        tmp_path,
        failed_pages_callback=failed_batches.append,
    )

    assert downloaded == 0
    assert failed_batches == [[1, 2, 3]]
    assert list(tmp_path.iterdir()) == []


def test_empty_response_body_counts_as_failure(tmp_path):
    """A 200 response with an empty body is not a resume-able success."""
    urls = page_urls(2)
    session = FakeSession(empty_urls={urls[0]})
    downloader = make_downloader(tmp_path, session)
    failed_batches: list[list[int]] = []

    downloaded = downloader.download_from_urls(
        urls,
        tmp_path,
        failed_pages_callback=failed_batches.append,
    )

    assert downloaded == 1
    assert failed_batches == [[1]]


def test_failed_pages_are_deduplicated_and_sorted():
    """Failure aggregation is ascending and free of duplicates."""
    assert normalize_page_numbers([5, 2, 5, 9, 2]) == [2, 5, 9]
    assert normalize_page_numbers([3, 1]) == [1, 3]
    assert normalize_page_numbers([]) == []


def test_failed_pages_callback_receives_sorted_deduplicated_list(tmp_path):
    """The callback receives the normalized list even when pages race."""
    urls = page_urls(6)
    session = FakeSession(failing_urls={urls[index] for index in (0, 2, 4)})
    downloader = make_downloader(tmp_path, session)
    failed_batches: list[list[int]] = []

    downloader.download_from_urls(
        urls,
        tmp_path,
        failed_pages_callback=failed_batches.append,
    )

    assert failed_batches == [[1, 3, 5]]


def test_no_urls_returns_zero_and_empty_failures(tmp_path):
    session = FakeSession()
    downloader = make_downloader(tmp_path, session)
    failed_batches: list[list[int]] = []

    downloaded = downloader.download_from_urls(
        [],
        tmp_path,
        failed_pages_callback=failed_batches.append,
    )

    assert downloaded == 0
    assert failed_batches == [[]]
    assert session.requested == []


def test_resume_does_not_recheck_image_content(tmp_path):
    """Resume trusts any non-empty file, even one with junk content."""
    (tmp_path / "001.webp").write_bytes(b"not-an-image")
    session = FakeSession()
    downloader = make_downloader(tmp_path, session)

    downloaded = downloader.download_from_urls(page_urls(1), tmp_path)

    assert downloaded == 1
    assert session.requested == []
    assert (tmp_path / "001.webp").read_bytes() == b"not-an-image"


def test_rerun_resumes_only_failed_pages(tmp_path):
    """A second run fetches only the pages that failed the first time."""
    urls = page_urls(4)

    first_session = FakeSession(failing_urls={urls[1]})
    first_downloader = make_downloader(tmp_path, first_session)
    first_failed: list[list[int]] = []
    first_count = first_downloader.download_from_urls(
        urls, tmp_path, failed_pages_callback=first_failed.append
    )

    assert first_count == 3
    assert first_failed == [[2]]

    second_session = FakeSession()
    second_downloader = make_downloader(tmp_path, second_session)
    second_failed: list[list[int]] = []
    second_count = second_downloader.download_from_urls(
        urls, tmp_path, failed_pages_callback=second_failed.append
    )

    assert second_count == 4
    assert second_failed == [[]]
    # Only the previously failed page is requested again
    assert second_session.requested == [urls[1]]
    assert sorted(p.stem for p in tmp_path.iterdir()) == ["001", "002", "003", "004"]


def test_partial_temp_file_is_not_treated_as_resumable(tmp_path):
    """An in-progress .part download must be re-fetched, not trusted."""
    (tmp_path / "001.webp.part").write_bytes(b"half-written")
    session = FakeSession()
    downloader = make_downloader(tmp_path, session)

    downloaded = downloader.download_from_urls(page_urls(1), tmp_path)

    assert downloaded == 1
    assert session.requested == page_urls(1)
    assert (tmp_path / "001.webp").read_bytes() == b"image-bytes"


def test_download_creates_missing_output_directory(tmp_path):
    nested = tmp_path / "missing" / "chapter"
    session = FakeSession()
    downloader = make_downloader(tmp_path, session)

    downloaded = downloader.download_from_urls(page_urls(2), nested)

    assert downloaded == 2
    assert nested.is_dir()
    assert sorted(p.stem for p in nested.iterdir()) == ["001", "002"]


@pytest.mark.parametrize(
    "success_pages, total_pages, expected",
    [
        (5, 5, CHAPTER_COMPLETE),
        (4, 5, CHAPTER_PARTIAL),
        (1, 5, CHAPTER_PARTIAL),
        (0, 5, CHAPTER_FAILED),
        (0, 0, CHAPTER_FAILED),
    ],
)
def test_classify_chapter_status(success_pages, total_pages, expected):
    assert classify_chapter_status(success_pages, total_pages) == expected
