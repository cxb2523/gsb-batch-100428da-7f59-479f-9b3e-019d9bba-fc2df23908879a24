import json
import time
import logging
import requests
import cv2
import numpy as np
from pathlib import Path
from urllib.parse import quote, quote_plus
from concurrent.futures import ThreadPoolExecutor

from .models import SearchResponse, BoardResponse
from .utils import (
    image_hash,
    ensure_dir,
    current_epoch_ms,
    is_allowed_image_url,
    safe_image_path,
    REQUEST_TIMEOUT,
    MAX_IMAGE_BYTES,
)


class Pinterest:
    BASE_URL = "https://in.pinterest.com"

    # Per-page outcome categories recorded on ``self.last_search``.
    PAGE_SUCCESS = "success"
    PAGE_HTTP_ERROR = "http_error"
    PAGE_PARSE_ERROR = "parse_error"
    PAGE_EMPTY_DATA = "empty_data"
    PAGE_NO_ORIG_IMAGE = "no_orig_image"

    def __init__(self, user_agent: str = "", proxies: dict = None, sleep_time: float = None):
        self.errors = []
        self.sleep_time = sleep_time
        self.proxies = proxies or {}
        self.unique_images = []
        # Outcome of the most recent ``search`` run (None until the first run).
        self.last_search = None

        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0"
        )

        self.BASE_HEADERS = {
            'Host': self.BASE_URL.replace('https://', ''),
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Ch-Ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
            'Sec-Ch-Ua-Model': '""',
            'Sec-Ch-Ua-Mobile': '?0',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/javascript, */*, q=0.01',
            'X-Pinterest-Source-Url': '',
            'X-Pinterest-Appstate': 'active',
            'Accept-Language': 'en-US,en;q=0.9',
            'Screen-Dpr': '1',
            'X-Pinterest-Pws-Handler': 'www/search/[scope].js',
            'User-Agent': self.user_agent,
            'Sec-Ch-Ua-Platform-Version': '""',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Dest': 'empty',
            'Referer': f'{self.BASE_URL}/',
            'Priority': 'u=1, i',
        }

        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)

        self.time_epoch = self._load_epoch()
        self.session = requests.Session()

    # -----------------------------
    # Epoch persistence
    # -----------------------------

    def _load_epoch(self) -> int:
        file = self.data_dir / "time_epoch.json"
        if file.exists():
            data = json.loads(file.read_text())
            saved = data.get("time_epoch")
            if saved:
                if float(saved) < current_epoch_ms():
                    return self._save_epoch()
                return int(saved)
        return self._save_epoch()

    def _save_epoch(self) -> int:
        epoch = current_epoch_ms()
        (self.data_dir / "time_epoch.json").write_text(json.dumps({"time_epoch": epoch}))
        return epoch

    # -----------------------------
    # Pinterest API calls
    # -----------------------------

    @staticmethod
    def _is_usable_bookmark(bookmark) -> bool:
        """A pagination cursor is only usable as a non-empty string.

        Pinterest returns ``"-end-"`` (and sometimes null/empty values) when
        there are no further pages, so those must not trigger another request.
        """
        return isinstance(bookmark, str) and bookmark not in ("", "-end-")

    def _build_search_url(self, query: str, page_size, bookmark=None) -> str:
        """Build a BaseSearchResource request URL using the existing
        source_url and ``data`` encoding scheme.

        The pagination cursor is only added to the payload when Pinterest
        actually supplied one; we never invent cursor parameters.
        """
        source_url = f"/search/pins/?q={quote(query)}&rs=typed"

        payload = {
            "options": {
                "applied_unified_filters": None,
                "appliedProductFilters": "---",
                "article": None,
                "auto_correction_disabled": False,
                "corpus": None,
                "customized_rerank_type": None,
                "domains": None,
                "filters": None,
                "journey_depth": None,
                "page_size": f"{page_size}",
                "price_max": None,
                "price_min": None,
                "query_pin_sigs": None,
                "query": quote(query),
                "redux_normalize_feed": True,
                "request_params": None,
                "rs": "typed",
                "scope": "pins",
                "selected_one_bar_modules": None,
                "source_id": None,
                "source_module_id": None,
                "seoDrawerEnabled": False,
                "source_url": quote_plus(source_url),
                "top_pin_id": None,
                "top_pin_ids": None
            },
            "context": {}
        }

        if self._is_usable_bookmark(bookmark):
            payload["options"]["bookmarks"] = [bookmark]

        encoded = quote_plus(json.dumps(payload).replace(" ", ""))
        encoded = (
            encoded.replace("%2520", "%20")
            .replace("%252F", "%2F")
            .replace("%253F", "%3F")
            .replace("%252520", "%2520")
            .replace("%253D", "%3D")
            .replace("%2526", "%26")
        )

        return (
            f"{self.BASE_URL}/resource/BaseSearchResource/get/"
            f"?source_url={quote_plus(source_url)}&data={encoded}&_={self.time_epoch}"
        )

    def _extract_bookmark_safely(self, response):
        """Best-effort extraction of a pagination cursor from a raw response
        (used for pages that failed before model validation)."""
        try:
            raw = response.json()
            data = (raw or {}).get("resource_response", {}).get("data")
            if isinstance(data, dict):
                bookmarks = data.get("bookmark")
                if self._is_usable_bookmark(bookmarks):
                    return bookmarks
        except Exception:
            pass
        return None

    def _fetch_search_page(self, url: str, source_url: str):
        """Request and parse one search page.

        Returns ``(outcome, image_urls, page_image_count, bookmark)`` where
        ``outcome`` is one of the PAGE_* categories. A failure on one page is
        contained here so the caller can keep paging with the next cursor.
        """
        headers = self.BASE_HEADERS.copy()
        headers["X-Pinterest-Source-Url"] = source_url

        try:
            response = self.session.get(
                url, headers=headers, proxies=self.proxies, timeout=REQUEST_TIMEOUT
            )
        except Exception as e:
            logging.warning(f"Image search request failed: {e}")
            return self.PAGE_HTTP_ERROR, [], 0, None

        if self.sleep_time:
            time.sleep(self.sleep_time)

        if response.status_code != 200:
            logging.warning(f"Image search failed: {response.status_code}")
            return (
                self.PAGE_HTTP_ERROR, [], 0,
                self._extract_bookmark_safely(response),
            )

        try:
            data = SearchResponse(**response.json())
        except Exception as e:
            logging.warning(f"Image search response could not be parsed: {e}")
            return (
                self.PAGE_PARSE_ERROR, [], 0,
                self._extract_bookmark_safely(response),
            )

        search_data = data.resource_response.data
        if not search_data or not search_data.results:
            logging.warning("Image search returned an empty resource_response.data")
            bookmark = search_data.bookmark if search_data is not None else None
            return self.PAGE_EMPTY_DATA, [], 0, bookmark if self._is_usable_bookmark(bookmark) else None

        image_urls = []
        for result in search_data.results:
            if not result.images:
                continue

            orig = result.images.get("orig")
            if not orig:
                continue

            image_urls.append(str(orig.url))

        if not image_urls:
            logging.warning("Image search page contained no usable orig images")
            bookmark = search_data.bookmark
            return (
                self.PAGE_NO_ORIG_IMAGE, [], 0,
                bookmark if self._is_usable_bookmark(bookmark) else None,
            )

        bookmark = search_data.bookmark
        return (
            self.PAGE_SUCCESS,
            image_urls,
            len(image_urls),
            bookmark if self._is_usable_bookmark(bookmark) else None,
        )

    def search(self, query: str, page_size=26, max_pages: int = 1):
        """Search Pinterest pins and return their ``orig.url`` image URLs.

        ``max_pages`` limits how many search pages are requested, following
        Pinterest's own pagination cursor. It defaults to 1 so the historical
        single-page behaviour is unchanged. Paging stops early when Pinterest
        stops returning a usable cursor.

        A failing page (non-200 HTTP status, unparseable JSON, empty
        ``resource_response.data`` or a page without any usable ``orig``
        image) is recorded on ``self.last_search`` and never discards images
        already collected from earlier pages.

        The returned list is ordered by first appearance across pages with
        cross-page duplicates removed. Per-run details (whether at least one
        image was found, how many pages were requested, each page's number and
        outcome category, and the cumulative image count of successful pages)
        are exposed on ``self.last_search``.
        """
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")

        source_url = f"/search/pins/?q={quote(query)}&rs=typed"

        # Warm-up request (critical) — once per search run, as before.
        self.session.get(f"{self.BASE_URL}{source_url}", headers=self.BASE_HEADERS, timeout=REQUEST_TIMEOUT)

        self.last_search = {
            "found_image": False,
            "pages_requested": 0,
            "page_results": [],
            "images_count": 0,
        }

        image_urls = []
        seen_urls = set()
        bookmark = None
        pages_requested = 0

        while pages_requested < max_pages:
            page_number = pages_requested + 1
            url = self._build_search_url(query, page_size, bookmark)
            outcome, page_urls, page_image_count, next_bookmark = self._fetch_search_page(
                url, source_url
            )
            pages_requested += 1

            if outcome == self.PAGE_SUCCESS:
                for image_url in page_urls:
                    if image_url not in seen_urls:
                        seen_urls.add(image_url)
                        image_urls.append(image_url)
                self.last_search["images_count"] += page_image_count

            self.last_search["page_results"].append({
                "page": page_number,
                "outcome": outcome,
                "images": page_image_count,
            })

            bookmark = next_bookmark
            # No usable cursor returned by Pinterest: stop, do not fabricate one.
            if not self._is_usable_bookmark(bookmark):
                break

        self.last_search["pages_requested"] = pages_requested
        self.last_search["found_image"] = bool(image_urls)

        return image_urls

    def get_pin_details(self, username: str, board: str):
        headers = self.BASE_HEADERS.copy()
        headers['x-pinterest-appstate'] = 'active'
        headers['x-pinterest-pws-handler'] = 'www/[username]/[slug].js'
        headers['x-pinterest-source-url'] = f'/{username}/{board}/'
        headers['x-requested-with'] = 'XMLHttpRequest'

        params = {
            'source_url': f'/{username}/{board}/',
            'data': json.dumps({"options": {"field_set_key": "profile", "username": username}, "context": {}}),
            '_': str(current_epoch_ms()),
        }

        response = self.session.get(
            f'{self.BASE_URL}/resource/UserResource/get/',
            params=params,
            headers=headers,
            proxies=self.proxies,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            return None

        data = BoardResponse(**response.json())
        return data.resource_response.data.created_at

    # -----------------------------
    # Image downloading
    # -----------------------------

    def _save_image(self, url: str, folder: Path):
        try:
            # SSRF guard: only fetch https URLs from trusted Pinterest CDN hosts.
            if not is_allowed_image_url(url):
                logging.warning(f"Refusing to download untrusted image URL: {url}")
                return

            # Path-traversal guard: derive a safe basename inside the target folder.
            filename = safe_image_path(url, folder)
            if filename is None:
                logging.warning(f"Refusing unsafe image filename for URL: {url}")
                return

            # Pinterest requires headers for image downloads
            headers = {
                "User-Agent": self.user_agent,
                "Referer": "https://www.pinterest.com/",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }

            response = self.session.get(
                url, headers=headers, proxies=self.proxies, timeout=REQUEST_TIMEOUT, stream=True
            )
            if response.status_code != 200:
                logging.warning(f"Failed to download image: {url} ({response.status_code})")
                return

            # Decompression-bomb guard: cap the number of bytes we read.
            content = response.raw.read(MAX_IMAGE_BYTES + 1, decode_content=True)
            if len(content) > MAX_IMAGE_BYTES:
                logging.warning(f"Image exceeds size cap, skipping: {url}")
                return

            img_arr = np.frombuffer(content, dtype=np.uint8)
            image = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)

            if image is None:
                logging.warning(f"cv2 failed to decode image: {url}")
                return

            h = image_hash(image)
            if h in self.unique_images:
                return

            cv2.imwrite(str(filename), image)
            self.unique_images.append(h)

            if self.sleep_time:
                time.sleep(self.sleep_time)

        except Exception as e:
            logging.error(f"Error saving image {url}: {e}")

    def download(self, url_list: list, number_of_workers: int, output_folder: str):
        folder = ensure_dir(output_folder)
        with ThreadPoolExecutor(max_workers=number_of_workers) as executor:
            executor.map(lambda u: self._save_image(u, folder), url_list)


if __name__ == "__main__":
    download_limit = 1
    keyword = "loki"

    p = Pinterest(sleep_time=2)

    print(f"\nSearching for: {keyword}")
    images_url = p.search(keyword, download_limit)
    print(f"Found {len(images_url)} image(s). Downloading…")

    p.download(url_list=images_url, number_of_workers=1, output_folder="output")

    print("\nFetching board details…")
    created_at = p.get_pin_details(username="canva", board="design-trends")
    print(f"Board created_at: {created_at}")
