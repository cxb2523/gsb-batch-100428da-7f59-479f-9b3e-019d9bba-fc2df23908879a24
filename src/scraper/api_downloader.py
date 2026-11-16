"""
API-based Chapter Downloader
Captures image URLs from network requests and downloads them directly
"""

import os
import re
import json
import time
from curl_cffi import requests
from pathlib import Path
from typing import Callable, Iterable, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .api_client import KaganeAPIClient
from .api_models import Book, Series


@dataclass
class DownloadResult:
    """Result of a chapter download"""
    book: Book
    success: bool
    chapter_dir: Path
    pages_downloaded: int
    error: Optional[str] = None


# Callback invoked once per settled page: (successful_pages, total_pages)
ProgressCallback = Callable[[int, int], None]
# Callback invoked once with the ascending, de-duplicated list of failed page numbers
FailedPagesCallback = Callable[[list[int]], None]

# Chapter settlement statuses. A chapter is "partial" when at least one page
# succeeded (downloaded now or resumed from disk) but some pages failed.
CHAPTER_COMPLETE = "complete"
CHAPTER_PARTIAL = "partial"
CHAPTER_FAILED = "failed"


def classify_chapter_status(success_pages: int, total_pages: int) -> str:
    """
    Classify a chapter from its settled page counts.

    Args:
        success_pages: Pages saved on disk (downloaded now or resumed)
        total_pages: Expected number of pages in the chapter

    Returns:
        One of CHAPTER_COMPLETE, CHAPTER_PARTIAL, CHAPTER_FAILED
    """
    if success_pages <= 0 or total_pages <= 0:
        return CHAPTER_FAILED
    if success_pages >= total_pages:
        return CHAPTER_COMPLETE
    return CHAPTER_PARTIAL


def normalize_page_numbers(pages: Iterable[int]) -> list[int]:
    """Return page numbers as an ascending list with duplicates removed."""
    unique_pages: set[int] = set()
    for page in pages:
        try:
            unique_pages.add(int(page))
        except (TypeError, ValueError):
            continue
    return sorted(unique_pages)


class APIChapterDownloader:
    """
    Downloads chapter images directly through the Kagane API.
    
    The approach:
    1. Request a short-lived chapter access token from the API
    2. Build the ordered page image URLs
    3. Download images concurrently
    """
    
    IMAGE_URL_PATTERN = "kstatic.to/api/v2/books/page/"
    
    def __init__(
        self,
        download_dir: Path,
        max_concurrent_images: int = 10,
        max_retries: int = 3,
        request_timeout: int = 30
    ):
        self.download_dir = download_dir
        self.max_concurrent_images = max_concurrent_images
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        
        # Pages that failed during the most recent download_from_urls call,
        # ascending and de-duplicated
        self.last_failed_pages: list[int] = []

        # Create a session for downloading
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "https://kagane.moe/",
            "Origin": "https://kagane.moe",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "image"
        })
        self.api = KaganeAPIClient()
    
    def get_page_urls(self, book_id: str) -> list[str]:
        """Fetch ordered page image URLs for a chapter from the API"""
        return self.api.get_page_urls(book_id)
    
    @staticmethod
    def sanitize_filename(name: str, max_length: int = 80) -> str:
        """Sanitize a string to be safe for Windows filenames"""
        sanitized = re.sub(r'[<>:"/\\|?*~\[\]{}]', '_', name)
        sanitized = re.sub(r'_+', '_', sanitized)
        sanitized = sanitized.strip(' _')
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length].rstrip(' _')
        return sanitized
    
    @staticmethod
    def page_filename(page_number: int) -> str:
        """Return the canonical (extension-less) filename for a page number."""
        return f"{page_number:03d}"

    @classmethod
    def _scan_page_files(cls, output_dir: Path) -> dict[int, list[Path]]:
        """Group files in ``output_dir`` by their numeric page-number stem."""
        grouped: dict[int, list[Path]] = {}
        try:
            entries = list(output_dir.iterdir())
        except OSError:
            return grouped

        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                # In-progress downloads are never a resumable page
                if entry.name.endswith('.part'):
                    continue
                if not entry.stem.isdigit():
                    continue
                grouped.setdefault(int(entry.stem), []).append(entry)
            except OSError:
                continue
        return grouped

    @staticmethod
    def _is_non_empty_file(path: Path) -> bool:
        """True when ``path`` is a regular file with at least one byte."""
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    @staticmethod
    def _remove_empty_page_files(page_files: list[Path]) -> None:
        """Delete zero-byte page files so stale placeholders are not reused."""
        for path in page_files:
            try:
                if path.stat().st_size == 0:
                    path.unlink()
            except OSError:
                continue

    def download_image(self, url: str, output_path: Path) -> bool:
        """Download a single image with retries.

        The payload is written to a temporary sibling file first and moved into
        place only after the response finished, so an interrupted download never
        leaves a truncated page that a later resume would trust.
        """
        for attempt in range(self.max_retries):
            temp_path: Optional[Path] = None
            try:
                response = self.session.get(url, timeout=self.request_timeout, stream=True, impersonate="chrome")
                response.raise_for_status()

                # Determine file extension from content-type or URL
                content_type = response.headers.get('content-type', '')
                if 'webp' in content_type:
                    ext = '.webp'
                elif 'jpeg' in content_type or 'jpg' in content_type:
                    ext = '.jpg'
                elif 'png' in content_type:
                    ext = '.png'
                else:
                    # Try to get from URL
                    if '.webp' in url.lower():
                        ext = '.webp'
                    elif '.jpg' in url.lower() or '.jpeg' in url.lower():
                        ext = '.jpg'
                    elif '.png' in url.lower():
                        ext = '.png'
                    else:
                        ext = '.webp'  # Default to webp

                # Update path with correct extension
                final_path = output_path.with_suffix(ext)
                temp_path = final_path.with_name(final_path.name + '.part')

                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                # An empty payload is a failure: resume only trusts non-empty files
                if temp_path.stat().st_size == 0:
                    raise ValueError(f"Empty response body for {url}")

                os.replace(temp_path, final_path)
                return True

            except Exception:
                if temp_path is not None:
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

        return False

    def download_from_urls(
        self,
        image_urls: list[str],
        output_dir: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        failed_pages_callback: Optional[FailedPagesCallback] = None,
    ) -> int:
        """
        Download images from a list of URLs, resuming already-saved pages.

        Pages are named by 1-based page number (001, 002, ...). Before fetching
        a page, an existing non-empty file with the same page-number stem in
        ``output_dir`` is reused and counted as a success. Only non-empty files
        are trusted; image content is not validated.

        A failing page never aborts the remaining pages.

        Args:
            image_urls: List of image URLs to download
            output_dir: Directory to save images
            progress_callback: Optional callback(current, total) called as each
                page settles (rescanned or downloaded)
            failed_pages_callback: Optional callback that receives the final
                ascending, de-duplicated list of failed page numbers (called
                once, also when the list is empty)

        Returns:
            Number of pages available on disk, including resumed pages
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        total = len(image_urls)
        downloaded = 0
        failed_pages: list[int] = []
        # Clear the previous settlement so a failure mid-call cannot leave stale data
        self.last_failed_pages = []

        # Existing page files, grouped by page number, are inspected only once
        existing_files = self._scan_page_files(output_dir)

        # Split the pages into "already on disk" and "still to fetch"
        pending: list[tuple[int, str]] = []
        for page_number, url in enumerate(image_urls, 1):
            page_files = existing_files.get(page_number, [])
            if any(self._is_non_empty_file(p) for p in page_files):
                downloaded += 1
                # Drop zero-byte siblings so conversion never chokes on them
                self._remove_empty_page_files(page_files)
                if progress_callback:
                    progress_callback(downloaded, total)
                continue

            # Zero-byte leftovers must not masquerade as a resumable page
            self._remove_empty_page_files(page_files)
            pending.append((page_number, url))

        if pending:
            with ThreadPoolExecutor(max_workers=max(1, self.max_concurrent_images)) as executor:
                futures = {
                    executor.submit(
                        self.download_image,
                        url,
                        output_dir / self.page_filename(page_number)
                    ): page_number
                    for page_number, url in pending
                }

                for future in as_completed(futures):
                    page_number = futures[future]
                    try:
                        succeeded = bool(future.result())
                    except Exception:
                        succeeded = False

                    if succeeded:
                        downloaded += 1
                        if progress_callback:
                            progress_callback(downloaded, total)
                    else:
                        failed_pages.append(page_number)
                        # download_image never leaves an empty final file behind,
                        # nothing to clean up here.

        settled_failed_pages = normalize_page_numbers(failed_pages)
        # Expose the settlement for callers that prefer reading state over a callback
        self.last_failed_pages = settled_failed_pages
        if failed_pages_callback:
            failed_pages_callback(list(settled_failed_pages))

        return downloaded
    
    def close(self):
        """Close the sessions"""
        self.api.close()
        self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_reader_url(series_id: str, book_id: str) -> str:
    """Generate the reader URL for a book"""
    return f"https://kagane.moe/series/{series_id}/reader/{book_id}"


def get_image_urls_from_browser(
    driver,
    series_id: str,
    book_id: str,
    wait_time: int = 15
) -> list[str]:
    """
    Capture image URLs from browser network logs.
    
    Args:
        driver: Selenium webdriver with Network logging enabled
        series_id: Series UUID
        book_id: Book UUID
        wait_time: Time to wait for images to load
    
    Returns:
        List of image URLs
    """
    reader_url = get_reader_url(series_id, book_id)
    
    # Navigate to reader
    driver.get(reader_url)
    
    # Wait for images to load
    time.sleep(wait_time)
    
    # Get network logs
    logs = driver.get_log("performance")
    
    image_urls = set()
    
    for entry in logs:
        try:
            log = json.loads(entry["message"])["message"]
            
            if log["method"] == "Network.requestWillBeSent":
                url = log["params"]["request"]["url"]
                
                if APIChapterDownloader.IMAGE_URL_PATTERN in url:
                    image_urls.add(url)
        except (json.JSONDecodeError, KeyError):
            continue
    
    return list(image_urls)
