#!/usr/bin/env python3
"""
Complete Website Source Code Downloader & Crawler
Author: Sagar Biswas

DISCLAIMER:
Educational and authorized testing only.
Respects server trust boundaries.
"""

import os
import sys
import socket
import argparse
import hashlib
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from bs4 import BeautifulSoup, FeatureNotFound


# Windows consoles may use a legacy code page (e.g. GBK/cp1252) that cannot
# encode the status glyphs. Reconfigure stdout to UTF-8 with replacement so
# a console encoding issue never aborts a crawl.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


USER_AGENT = "Mozilla/5.0 (Educational Website Crawler)"
TIMEOUT = 10
visited_lock = threading.Lock()

FAILURE_REPORT_NAME = "failures.txt"


# -----------------------------
# Failure tracking
# -----------------------------

def _safe_str(value) -> str:
    """Stringify an exception detail without crashing on legacy code pages."""
    try:
        return str(value)
    except Exception:
        try:
            return repr(value)
        except Exception:
            return value.__class__.__name__


def classify_error(exc: Exception) -> tuple[str, str]:
    """Map a download exception to a stable (category, detail) pair."""
    if isinstance(exc, HTTPError):
        return "http-error", f"HTTP {exc.code}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout", _safe_str(exc) or "request timed out"
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timeout", _safe_str(reason) or "request timed out"
        return "connection-error", _safe_str(reason)
    if isinstance(exc, (ConnectionError, OSError)):
        return "connection-error", _safe_str(exc) or exc.__class__.__name__
    return "download-error", _safe_str(exc) or exc.__class__.__name__


class FailureTracker:
    """Thread-safe record of every failed asset and failed page.

    Every failure is bound to the page that owned the request, so
    concurrent workers can never attribute an object to the wrong page.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # page_url -> list of (asset_url, category, detail)
        self.asset_failures: dict[str, list[tuple[str, str, str]]] = {}
        # page_url -> list of (category, detail)
        self.page_failures: dict[str, list[tuple[str, str]]] = {}

    def record_asset_failure(self, page_url: str, asset_url: str, exc: Exception) -> tuple[str, str]:
        category, detail = classify_error(exc)
        with self._lock:
            self.asset_failures.setdefault(page_url, []).append(
                (asset_url, category, detail)
            )
        return category, detail

    def record_page_failure(self, page_url: str, exc: Exception) -> tuple[str, str]:
        category, detail = classify_error(exc)
        with self._lock:
            self.page_failures.setdefault(page_url, []).append((category, detail))
        return category, detail

    def has_failures(self) -> bool:
        with self._lock:
            return bool(self.asset_failures or self.page_failures)

    def render_report(self) -> list[str]:
        """Build the failure manifest lines (identical for file and terminal)."""
        with self._lock:
            asset_snapshot = {
                page: sorted(entries) for page, entries in self.asset_failures.items()
            }
            page_snapshot = {
                page: sorted(entries) for page, entries in self.page_failures.items()
            }

        total_assets = sum(len(entries) for entries in asset_snapshot.values())
        total_pages = sum(len(entries) for entries in page_snapshot.values())

        lines = [
            "Failure report",
            f"Total failed resources: {total_assets}",
            f"Total failed pages: {total_pages}",
        ]

        for page_url in sorted(set(asset_snapshot) | set(page_snapshot)):
            lines.append("")
            lines.append(f"Page: {page_url}")

            assets = asset_snapshot.get(page_url, [])
            pages = page_snapshot.get(page_url, [])
            lines.append(
                f"  Failed resources: {len(assets)} | Failed page entries: {len(pages)}"
            )

            for asset_url, category, detail in assets:
                lines.append(f"  [resource] {asset_url} -> {category} ({detail})")
            for category, detail in pages:
                lines.append(f"  [page] {category} ({detail})")

        return lines


# -----------------------------
# Networking helpers
# -----------------------------

def safe_request(url: str):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    return urlopen(req, timeout=TIMEOUT)


def is_same_origin(base: str, target: str) -> bool:
    return urlparse(base).netloc == urlparse(target).netloc


# -----------------------------
# File helpers
# -----------------------------

def _guess_extension(content_type: str | None) -> str | None:
    if not content_type:
        return None

    content_type = content_type.split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
        "image/x-icon": ".ico",
        "application/pdf": ".pdf",
        "text/css": ".css",
        "application/javascript": ".js",
        "text/javascript": ".js",
    }
    return mapping.get(content_type)


def _append_query_suffix(path: str, query: str) -> str:
    if not query:
        return path

    stem, ext = os.path.splitext(path)
    suffix = hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]
    return f"{stem}__{suffix}{ext}"


def sanitize_path(url: str, base_dir: str, content_type: str | None = None) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path).lstrip("/")

    if not path or path.endswith("/"):
        path = os.path.join(path, "index")

    ext = os.path.splitext(path)[1]
    if not ext:
        guessed = _guess_extension(content_type)
        if guessed:
            path = f"{path}{guessed}"
        else:
            path = f"{path}.html"

    path = _append_query_suffix(path, parsed.query)

    return os.path.join(base_dir, path)


def save_file(
    url: str,
    base_dir: str,
    page_url: str | None = None,
    failures: FailureTracker | None = None,
) -> str | None:
    """Download and save one asset.

    On any HTTP error, timeout or connection failure the failure is
    recorded against ``page_url`` and ``None`` is returned, so the rest
    of the page's resources keep being processed.
    """
    try:
        response = safe_request(url)
        content_type = response.headers.get("Content-Type", "")
        local_path = sanitize_path(url, base_dir, content_type)

        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        with open(local_path, "wb") as f:
            f.write(response.read())

        return local_path

    except Exception as exc:
        # Network-level failures only: the file-write above happens after
        # this point and is left to propagate like the original behavior.
        if isinstance(exc, (HTTPError, URLError, OSError)):
            if failures is not None and page_url is not None:
                failures.record_asset_failure(page_url, url, exc)
            return None
        raise


# -----------------------------
# HTML processing
# -----------------------------

def extract_assets(soup: BeautifulSoup, base_url: str) -> set[str]:
    assets = set()

    for tag in soup.find_all(["link", "script", "img"]):
        attr = "href" if tag.name == "link" else "src"
        value = tag.get(attr)
        if not value:
            continue
        if isinstance(value, list):
            value = value[0] if value else ""
        if value:
            assets.add(urljoin(base_url, str(value)))

    for tag in soup.find_all(["img", "source"]):
        srcset_value = tag.get("srcset")
        if not srcset_value:
            continue
        if isinstance(srcset_value, list):
            srcset_value = " ".join(srcset_value)
        srcset_value = str(srcset_value)
        for entry in srcset_value.split(","):
            url_part = entry.strip().split(" ", 1)[0]
            if url_part:
                assets.add(urljoin(base_url, url_part))

    return assets


def extract_links(soup: BeautifulSoup, base_url: str) -> set[str]:
    links = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if isinstance(href, list):
            href = href[0] if href else ""
        href = str(href) if href else ""
        if not href:
            continue
        if href.startswith("#"):
            continue

        full = urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            continue

        parsed = urlparse(full)
        clean = parsed._replace(fragment="").geturl()
        links.add(clean)

    return links


def rewrite_links(
    soup: BeautifulSoup,
    page_url: str,
    base_dir: str,
    asset_map: dict[str, str] | None = None,
):
    page_path = sanitize_path(page_url, base_dir)
    page_dir = os.path.dirname(page_path)

    for tag in soup.find_all(["link", "script", "img"]):
        attr = "href" if tag.name == "link" else "src"
        value = tag.get(attr)
        if not value:
            continue
        if isinstance(value, list):
            value = value[0] if value else ""
        value = str(value) if value else ""
        if not value:
            continue

        full_url = urljoin(page_url, value)

        if not is_same_origin(page_url, full_url):
            continue

        if asset_map and full_url in asset_map:
            local_path = asset_map[full_url]
        else:
            local_path = sanitize_path(full_url, base_dir)

        if not os.path.exists(local_path):
            continue

        try:
            relative_path = os.path.relpath(local_path, page_dir)
            tag[attr] = relative_path.replace("\\", "/")
        except ValueError:
            tag[attr] = "/" + os.path.relpath(local_path, base_dir).replace("\\", "/")

    for tag in soup.find_all(["img", "source"]):
        srcset_value = tag.get("srcset")
        if not srcset_value:
            continue
        if isinstance(srcset_value, list):
            srcset_value = " ".join(srcset_value)
        srcset_value = str(srcset_value)

        rewritten_entries: list[str] = []
        for entry in srcset_value.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(" ", 1)
            url_part = parts[0]
            descriptor = parts[1] if len(parts) > 1 else ""

            full_url = urljoin(page_url, url_part)
            if not is_same_origin(page_url, full_url):
                rewritten_entries.append(entry)
                continue

            if asset_map and full_url in asset_map:
                local_path = asset_map[full_url]
            else:
                local_path = sanitize_path(full_url, base_dir)

            if not os.path.exists(local_path):
                rewritten_entries.append(entry)
                continue

            relative_path = os.path.relpath(local_path, page_dir).replace("\\", "/")
            rewritten_entries.append(f"{relative_path} {descriptor}".strip())

        if rewritten_entries:
            tag["srcset"] = ", ".join(rewritten_entries)


# -----------------------------
# Crawl + Analyze Engine
# -----------------------------

def crawl_page(args):
    url, depth, base_url, project_dir, min_depth, max_depth, no_download, failures = args

    with visited_lock:
        if url in visited or depth > max_depth:
            return []
        visited.add(url)
    
    try:
        response = safe_request(url)
        content_type = response.headers.get("Content-Type", "")
        normalized_type = content_type.split(";", 1)[0].strip().lower()

        is_xml = url.lower().endswith(".xml") or normalized_type in {"application/xml", "text/xml"}
        is_html = normalized_type in {"text/html", "application/xhtml+xml"}

        if not is_html and not is_xml:
            local_path = sanitize_path(url, project_dir, content_type)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(response.read())
            print(f"[✓] Saved asset: {url}")
            return []

        html = response.read()
        if is_xml:
            try:
                soup = BeautifulSoup(html, "xml")
            except FeatureNotFound:
                soup = BeautifulSoup(html, "html.parser")
        else:
            soup = BeautifulSoup(html, "html.parser")

        new_links = []
        for link in extract_links(soup, url):
            if is_same_origin(base_url, link):
                new_links.append((link, depth + 1))

        if min_depth <= depth <= max_depth:
            if not no_download:
                assets = extract_assets(soup, url)
                asset_map = {}
                for asset in assets:
                    # A failed download is recorded inside save_file and
                    # never interrupts the remaining assets of this page.
                    local_path = save_file(asset, project_dir, url, failures)
                    if local_path:
                        asset_map[asset] = local_path

                rewrite_links(soup, url, project_dir, asset_map)

                page_path = sanitize_path(url, project_dir)
                os.makedirs(os.path.dirname(page_path), exist_ok=True)
                with open(page_path, "w", encoding="utf-8", errors="ignore") as f:
                    f.write(str(soup))

            print(f"[✓] Analyzed (depth {depth}): {url}")
        else:
            print(f"[•] Crawled only (depth {depth}): {url}")

        return new_links

    except Exception as e:
        # The page itself could not be fetched/parsed: record it and do
        # NOT write any local file for this page.
        failures.record_page_failure(url, e)
        print(f"[!] Error at {url}: {e}")
        return []


def crawl_website(base_url: str, min_depth: int, max_depth: int, export_urls: bool = False, no_download: bool = False, threads: int = 1):
    global visited
    visited = set()

    parsed = urlparse(base_url)
    # Replace characters that are illegal in directory names (e.g. the
    # colon of a port such as 127.0.0.1:8000). Ordinary hostnames keep
    # their original dot-to-underscore layout.
    safe_netloc = parsed.netloc.replace(".", "_")
    for illegal_char in ':?*<>|"':
        safe_netloc = safe_netloc.replace(illegal_char, "_")
    project_dir = safe_netloc
    os.makedirs(project_dir, exist_ok=True)

    # Shared, thread-safe failure ledger. Every entry is bound to the
    # page that owned the request, so concurrent workers cannot mix up
    # ownership or double-count.
    failures = FailureTracker()

    # URLs already sitting in the queue, so two concurrent pages linking
    # to the same target cannot enqueue it twice (which would process and
    # count that page twice). `visited` still tracks actually-processed
    # pages and keeps its original export/guard semantics.
    queued = {base_url}

    def enqueue(link: str, link_depth: int) -> None:
        if link_depth > max_depth:
            return
        with visited_lock:
            if link in visited or link in queued:
                return
            queued.add(link)
        queue.append((link, link_depth))

    queue = deque([(base_url, 0)])

    while queue:
        batch = []
        if threads > 1:
            for _ in range(min(threads, len(queue))):
                if queue:
                    batch.append(queue.popleft())
        else:
            batch.append(queue.popleft())

        page_args = [
            (url, depth, base_url, project_dir, min_depth, max_depth, no_download, failures)
            for url, depth in batch
        ]

        if threads > 1 and len(batch) > 1:
            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = [executor.submit(crawl_page, args) for args in page_args]
                for future in as_completed(futures):
                    for link, depth in future.result():
                        enqueue(link, depth)
        else:
            for args in page_args:
                results = crawl_page(args)
                for link, d in results:
                    enqueue(link, d)

    if export_urls:
        urls_file = os.path.join(project_dir, "urls.txt")
        with open(urls_file, "w", encoding="utf-8") as f:
            for url in sorted(visited):
                f.write(url + "\n")
        print(f"[✓] Exported {len(visited)} URLs to {urls_file}")

    if failures.has_failures():
        report_lines = failures.render_report()
        report_path = os.path.join(project_dir, FAILURE_REPORT_NAME)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines) + "\n")
        # The terminal output is the exact same content as the manifest.
        print("\n".join(report_lines))
        print(f"[✓] Failure report written to {report_path}")


# -----------------------------
# CLI
# -----------------------------

def parse_depth(depth_str: str) -> tuple[int, int]:
    if "-" in depth_str:
        min_d, max_d = depth_str.split("-", 1)
        return int(min_d), int(max_d)
    else:
        d = int(depth_str)
        return 0, d


def main():
    parser = argparse.ArgumentParser(description="Educational Website Source Crawler")
    parser.add_argument("url", help="Base URL (include http:// or https://)")
    parser.add_argument(
        "--depth",
        default="0",
        help="Crawl depth (e.g. 2 or 1-2)"
    )
    parser.add_argument(
        "--export-urls",
        action="store_true",
        help="Export visited URLs to urls.txt"
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Only crawl and export URLs, skip downloading files"
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Number of threads for concurrent crawling (default: 1)"
    )

    args = parser.parse_args()

    if not args.url.startswith(("http://", "https://")):
        print("[!] Invalid URL format.")
        sys.exit(1)

    min_depth, max_depth = parse_depth(args.depth)

    crawl_website(args.url, min_depth, max_depth, args.export_urls, args.no_download, args.threads)


if __name__ == "__main__":
    main()
