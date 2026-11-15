"""
Playwright-based crawler for jsrip, adapted and expanded for better coverage.
- Same-registrable-domain scope
- Discovers external + inline JS via network hooks & DOM
- Light interactions (scroll + safe anchor clicks + buttons + tabs) to surface lazy content
- Breadth-first queue with concurrency for deeper coverage
- Sitemap.xml + robots.txt discovery
- Webpack chunk discovery from JS content
- Source map downloading
- iframe content extraction
"""

import os
import re
import asyncio
import hashlib
import random
from urllib.parse import urlparse, urljoin
from collections import deque, defaultdict
from pathlib import Path

from playwright.async_api import async_playwright
from playwright._impl._errors import TimeoutError as PWTimeoutError

try:
    import aiohttp
except Exception:
    aiohttp = None

try:
    import tldextract
    # include_psl_private_domains: treat herokuapp.com / vercel.app / github.io
    # as suffixes, so one tenant never scopes to every other tenant on the host.
    # suffix_list_urls=(): use the bundled PSL snapshot, never fetch at runtime.
    _tld = tldextract.TLDExtract(include_psl_private_domains=True, suffix_list_urls=())
except Exception:
    tldextract = None
    _tld = None

try:
    import jsbeautifier
except Exception:
    jsbeautifier = None

JS_CT_HINTS = (
    "application/javascript",
    "application/x-javascript",
    "text/javascript",
    "text/ecmascript",
    "application/ecmascript",
    "application/x-ecmascript",
    "text/js",
)

VENDOR_HOST_DENY = {
    "www.google-analytics.com",
    "www.googletagmanager.com",
    "connect.facebook.net",
    "static.cloudflareinsights.com",
    "challenges.cloudflare.com",
    "www.recaptcha.net", "www.google.com",
    "bat.bing.com",
}

SMAP_RE = re.compile(r'^\s*//#\s*sourceMappingURL=([^\s]+)\s*$', re.MULTILINE)

# Webpack chunk discovery patterns
WEBPACK_CHUNK_PATTERNS = [
    re.compile(r'["\'](?:static/(?:js|chunks?)/|_next/static/chunks/|assets/js/)([a-zA-Z0-9._-]+\.(?:js|mjs))["\']'),
    re.compile(r'(?:__webpack_require__|webpackJsonp|__next_f)\s*[.(]\s*["\']?([0-9a-f]+)["\']?'),
    re.compile(r'["\']([a-f0-9]{8,20})\.\w+\.(?:js|chunk\.js)["\']'),
    re.compile(r'(?:e\.p|[a-z]\.p)\s*\+\s*["\']([^"\']+\.js)["\']'),
]


def registrable_domain(host: str) -> str:
    if not host:
        return ""
    if _tld:
        try:
            ext = _tld(host)
            return f"{ext.domain}.{ext.suffix}" if ext.suffix else host
        except Exception:
            pass
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def in_scope(url: str, base_regs) -> bool:
    try:
        u = urlparse(url)
        if not u.scheme.startswith("http"):
            return False
        host = u.hostname or ""
        rd = registrable_domain(host)
        if isinstance(base_regs, set):
            return rd in base_regs
        return rd == base_regs
    except Exception:
        return False


def resolve_link_base(requested_url: str, page_url: str, base_regs):
    """Base URL for urljoin-ing harvested links.

    Returns the post-redirect URL, or None when the page landed off-scope —
    harvesting there would graft another site's relative paths onto our host,
    producing phantom in-scope URLs that burn the page budget.
    """
    final = page_url or requested_url
    if not in_scope(final, base_regs):
        return None
    return final


async def run_queue_workers(q, concurrency, handle, should_continue):
    """Drain `q` with `concurrency` workers.

    Every dequeued item is task_done()'d — including items skipped after
    should_continue() goes False — so q.join() can never deadlock. Workers
    block on get() rather than polling q.empty(), so a transient empty queue
    doesn't end the crawl while peers are still enqueuing.
    """
    async def worker():
        while True:
            item = await q.get()
            try:
                if should_continue():
                    await handle(item)
            finally:
                q.task_done()

    tasks = [asyncio.create_task(worker()) for _ in range(max(1, concurrency))]
    try:
        await q.join()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def safe_filename_from_url(u: str, fallback_hash: str, suffix: str) -> str:
    p = urlparse(u)
    name = os.path.basename(p.path) or ""
    name = name.split("?")[0]
    if not name:
        name = f"{fallback_hash}{suffix}"
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name)
    return name[:180]


class PlaywrightCrawler:
    """
    Public API:
        await PlaywrightCrawler(config, logger).crawl_urls(urls) -> list[dict]
    Returns list of JS file records:
        {url, filepath, filename, size, sha256, source_page}
    """

    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger

        # IO
        self.output_dir = Path(config['output_dir'])
        self.js_dir = self.output_dir / "javascript"
        self.maps_dir = self.output_dir / "sourcemaps"
        self.meta_dir = self.output_dir / "playwright" / "meta"
        for d in (self.js_dir, self.maps_dir, self.meta_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Settings
        self.headless = bool(config.get("headless", True))
        self.max_pages = int(config.get("max_pages", 500))
        self.max_depth = int(config.get("max_depth", 2))
        self.user_agent = config.get("user_agent") or "jsrip/1.0"
        self.timeout_ms = int(config.get("timeout", 30)) * 1000
        self.enable_beautify = bool(config.get("beautify", True))
        self.enable_interactions = bool(config.get("enable_interactions", True))
        self.max_concurrency = int(config.get("max_concurrency", 4))

        # State
        self.base_reg = None
        self.visited_pages = set()
        self.downloaded_hashes = set()
        self.js_files = []
        self.all_endpoints = defaultdict(set)
        self.inline_counter = 0
        self._aio_session = None

    async def _get_aio_session(self):
        """Lazy-init aiohttp session."""
        if self._aio_session is None and aiohttp:
            timeout = aiohttp.ClientTimeout(total=15)
            self._aio_session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": self.user_agent}
            )
        return self._aio_session

    async def _close_aio_session(self):
        if self._aio_session:
            await self._aio_session.close()
            self._aio_session = None

    async def _fetch_url(self, url, as_text=True):
        """Fetch a URL with aiohttp, returns text or bytes."""
        session = await self._get_aio_session()
        if not session:
            return None
        try:
            async with session.get(url, ssl=False) as resp:
                if resp.status == 200:
                    return await resp.text() if as_text else await resp.read()
        except Exception as e:
            self.logger.debug(f"Fetch failed {url}: {e}")
        return None

    async def crawl_urls(self, urls):
        seeds = []
        for u in (urls or []):
            if u.startswith(("http://", "https://")):
                seeds.append(u)
            elif u:
                seeds.append(f"https://{u}")

        if not seeds:
            self.logger.warning("No valid host/URLs to crawl.")
            return []

        # Build scope from ALL seed URLs (supports multi-domain lists)
        self.base_reg = set()
        for s in seeds:
            host = urlparse(s).hostname or ""
            rd = registrable_domain(host)
            if rd:
                self.base_reg.add(rd)
        self.logger.info(f"Scope domains: {', '.join(sorted(self.base_reg))}")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=["--disable-web-security", "--no-sandbox", "--disable-dev-shm-usage"]
                )

                context = await browser.new_context(
                    ignore_https_errors=True,
                    user_agent=self.user_agent,
                    java_script_enabled=True,
                    bypass_csp=True,
                    extra_http_headers=self.config.get("headers") or {},
                    viewport={'width': 1366, 'height': 768},
                )

                if self.config.get("cookies"):
                    try:
                        await context.add_cookies(self.config["cookies"])
                    except Exception as e:
                        self.logger.debug(f"Failed to add cookies: {e}")

                try:
                    context.set_default_timeout(self.timeout_ms)
                except Exception:
                    pass

                # Queue + workers for breadth-first crawl
                q = asyncio.Queue()
                for u in seeds:
                    await q.put((u, 0))

                # Discover URLs from sitemap.xml / robots.txt for each seed domain
                all_sitemap_urls = set()
                seen_bases = set()
                for s in seeds:
                    p = urlparse(s)
                    base = f"{p.scheme}://{p.hostname}"
                    if p.port and p.port not in (80, 443):
                        base += f":{p.port}"
                    if base not in seen_bases:
                        seen_bases.add(base)
                        sm_urls = await self._discover_from_sitemap(s)
                        all_sitemap_urls.update(sm_urls)
                for u in all_sitemap_urls:
                    if u not in self.visited_pages:
                        await q.put((u, 0))
                if all_sitemap_urls:
                    self.logger.info(f"Sitemap discovery: found {len(all_sitemap_urls)} URLs")

                async def handle(item):
                    url, depth = item
                    try:
                        await self._visit(context, url, depth, q)
                    except Exception as e:
                        self.logger.debug(f"Visit error {url}: {e}")

                await run_queue_workers(
                    q,
                    self.max_concurrency,
                    handle,
                    lambda: len(self.visited_pages) < self.max_pages,
                )

                await context.close()
                await browser.close()

        except Exception as e:
            self.logger.warning(f"Crawler error: {e}")

        await self._close_aio_session()

        self.logger.info(f"Crawling complete. Found {len(self.js_files)} JavaScript files")
        return self.js_files

    # ===== Sitemap / Robots.txt Discovery =====

    async def _discover_from_sitemap(self, seed_url):
        """Discover URLs from robots.txt and sitemap.xml."""
        discovered = set()
        parsed = urlparse(seed_url)
        base = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port and parsed.port not in (80, 443):
            base += f":{parsed.port}"

        sitemap_urls = [f"{base}/sitemap.xml"]

        # Try robots.txt first for Sitemap: directives
        robots_content = await self._fetch_url(f"{base}/robots.txt")
        if robots_content:
            for line in robots_content.splitlines():
                line = line.strip()
                if line.lower().startswith("sitemap:"):
                    sm_url = line.split(":", 1)[1].strip()
                    if sm_url.startswith("http"):
                        sitemap_urls.append(sm_url)

        # Parse sitemaps
        for sm_url in sitemap_urls:
            content = await self._fetch_url(sm_url)
            if not content:
                continue
            # Extract <loc> tags
            locs = re.findall(r'<loc>\s*(.*?)\s*</loc>', content, re.IGNORECASE)
            for loc in locs:
                loc = loc.strip()
                if not loc:
                    continue
                # Check if it's a sitemap index (nested sitemaps)
                if loc.endswith('.xml') or 'sitemap' in loc.lower():
                    nested_content = await self._fetch_url(loc)
                    if nested_content:
                        nested_locs = re.findall(r'<loc>\s*(.*?)\s*</loc>', nested_content, re.IGNORECASE)
                        for nl in nested_locs:
                            nl = nl.strip()
                            if nl and in_scope(nl, self.base_reg):
                                discovered.add(nl)
                elif in_scope(loc, self.base_reg):
                    discovered.add(loc)

        return list(discovered)[:200]  # Cap to avoid huge sitemaps

    # ===== Page Visit =====

    async def _visit(self, context, url: str, depth: int, q: asyncio.Queue):
        if depth > self.max_depth:
            return
        if url in self.visited_pages:
            return
        if len(self.visited_pages) >= self.max_pages:
            return
        if not in_scope(url, self.base_reg):
            return

        self.visited_pages.add(url)
        self.logger.info(f"Crawling: {url} (depth: {depth})")

        page = await context.new_page()
        page.on("response", lambda resp: asyncio.create_task(self._on_response(resp)))
        page.on("request", lambda req: self._on_request(req))

        try:
            try:
                page.set_default_navigation_timeout(self.timeout_ms)
                page.set_default_timeout(self.timeout_ms)
            except Exception:
                pass

            waits = ["networkidle", "load", "domcontentloaded", "commit"]

            async def _nav_once():
                last_exc = None
                for w in waits:
                    try:
                        await page.goto(url, wait_until=w, timeout=self.timeout_ms)
                        if w == "commit":
                            try:
                                await page.wait_for_selector("body", timeout=3000)
                            except Exception:
                                pass
                        return
                    except PWTimeoutError as te:
                        last_exc = te
                    except Exception as e:
                        last_exc = e
                if last_exc:
                    raise last_exc

            try:
                await _nav_once()
            except Exception as nav_exc:
                self.logger.debug(f"Nav failed {url}: {nav_exc}")
                return

            # Light settle & optional interactions
            try:
                await page.wait_for_load_state("networkidle", timeout=1500)
            except Exception:
                pass

            if self.enable_interactions:
                await self._interact(page)

            # Extract and schedule. Use the post-redirect URL as the urljoin
            # base, and bail out entirely if the page landed off-scope.
            link_base = resolve_link_base(url, page.url, self.base_reg)
            if link_base is None:
                self.logger.debug(
                    f"Redirected off-scope, not harvesting: {url} -> {page.url}")
                return

            links = await self._extract_links(page, base=link_base)
            scripts = await self._extract_script_tags(page, base=link_base)

            for l in links:
                if in_scope(l, self.base_reg) and l not in self.visited_pages:
                    await q.put((l, depth + 1))

            for s in scripts:
                if in_scope(s, self.base_reg):
                    pass

            await self._save_inline_scripts(page, source_url=url)

            # Extract iframe scripts
            await self._extract_iframe_scripts(page, source_url=url)

            # Discover worker scripts
            await self._discover_workers(page, source_url=url)
        finally:
            await page.close()

    async def _interact(self, page):
        # Gentle scroll
        try:
            for _ in range(2):
                await page.mouse.wheel(0, 1200)
                await asyncio.sleep(0.4)
        except Exception:
            pass

        # Click a few visible anchors safely
        dangerous_terms = ("logout", "signout", "delete", "remove", "cancel", "close", "unsubscribe")
        try:
            anchors = await page.locator("a:visible").all()
        except Exception:
            anchors = []
        for a in anchors[:12]:
            try:
                txt = (await a.inner_text(timeout=800)).lower() if await a.is_visible(timeout=800) else ""
                href = await a.get_attribute("href") or ""
                if any(b in (txt + href).lower() for b in dangerous_terms):
                    continue
                await a.click(timeout=800)
                try:
                    await page.wait_for_load_state("networkidle", timeout=1000)
                except Exception:
                    pass
            except Exception:
                pass

        # Click visible buttons (to trigger dynamic content loading)
        try:
            buttons = await page.locator("button:visible").all()
        except Exception:
            buttons = []
        for btn in buttons[:8]:
            try:
                txt = (await btn.inner_text(timeout=500)).lower()
                if any(b in txt for b in dangerous_terms):
                    continue
                if any(kw in txt for kw in ("load more", "show more", "view all", "expand", "more", "next")):
                    await btn.click(timeout=800)
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # Click tab-like elements to reveal tabbed content
        try:
            tabs = await page.locator("[role='tab']:visible").all()
        except Exception:
            tabs = []
        for tab in tabs[:6]:
            try:
                await tab.click(timeout=500)
                await asyncio.sleep(0.3)
            except Exception:
                pass

        # Open <details> elements
        try:
            details = await page.locator("details:not([open])").all()
        except Exception:
            details = []
        for d in details[:5]:
            try:
                summary = d.locator("summary")
                await summary.click(timeout=500)
            except Exception:
                pass

    def _on_request(self, req):
        try:
            url = req.url or ""
            if not in_scope(url, self.base_reg):
                return
            host = (urlparse(url).hostname or "").lower()
            if host in VENDOR_HOST_DENY:
                return
            rtype = (req.resource_type or "").lower()
            if rtype in ("xhr", "fetch"):
                self.all_endpoints["api"].add(url)
            elif rtype == "websocket":
                self.all_endpoints["websocket"].add(url)
        except Exception:
            pass

    async def _on_response(self, resp):
        try:
            url = resp.url
            status = resp.status
            ct = (resp.headers.get("content-type") or "").lower()
            if not in_scope(url, self.base_reg):
                return
            host = (urlparse(url).hostname or "").lower()
            if host in VENDOR_HOST_DENY:
                return
            if status < 200 or status >= 300:
                return
            path_is_js = urlparse(url).path.lower().endswith((".js", ".mjs", ".cjs"))
            if path_is_js or any(h in ct for h in JS_CT_HINTS):
                await self._save_js(url, resp)
        except Exception:
            pass

    async def _save_js(self, js_url: str, resp):
        try:
            body = await resp.body()
        except Exception:
            return

        txt = body.decode("utf-8", errors="ignore")
        if self.enable_beautify and jsbeautifier:
            try:
                txt = jsbeautifier.beautify(txt)
            except Exception:
                pass

        sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()
        if sha in self.downloaded_hashes:
            return
        self.downloaded_hashes.add(sha)

        fname = safe_filename_from_url(js_url, sha, ".js")
        fpath = self.js_dir / fname

        source_page = ""
        try:
            frame = resp.request.frame
            if frame and frame.url:
                source_page = frame.url
        except Exception:
            pass

        with open(fpath, "w", encoding="utf-8") as f:
            f.write(txt)

        self.js_files.append({
            "url": js_url,
            "filepath": str(fpath),
            "filename": fpath.name,
            "size": len(body),
            "sha256": sha,
            "source_page": source_page,
        })

        # Source map discovery and download
        await self._download_source_map(js_url, body, resp)

        # Webpack chunk discovery
        await self._discover_webpack_chunks(js_url, txt)

    async def _download_source_map(self, js_url, body, resp):
        """Download source maps if found."""
        sm_url = None
        try:
            sm_hdr = resp.headers.get("sourcemap") or resp.headers.get("x-sourcemap")
            if sm_hdr:
                sm_url = urljoin(js_url, sm_hdr)
            else:
                txt = body.decode("utf-8", errors="ignore")
                m = SMAP_RE.search(txt)
                if m:
                    raw = m.group(1).strip()
                    if not raw.startswith("data:"):
                        sm_url = urljoin(js_url, raw)
        except Exception:
            return

        if not sm_url:
            return

        try:
            sm_data = await self._fetch_url(sm_url, as_text=False)
            if sm_data and len(sm_data) > 10:
                sm_name = safe_filename_from_url(sm_url, hashlib.sha256(sm_data).hexdigest()[:12], ".map")
                sm_path = self.maps_dir / sm_name
                with open(sm_path, "wb") as f:
                    f.write(sm_data if isinstance(sm_data, bytes) else sm_data.encode("utf-8"))
                self.logger.debug(f"Source map saved: {sm_name}")
        except Exception as e:
            self.logger.debug(f"Source map download failed {sm_url}: {e}")

    async def _discover_webpack_chunks(self, js_url, content):
        """Discover and fetch webpack chunks referenced in JS content."""
        parsed = urlparse(js_url)
        base_path = parsed.path.rsplit("/", 1)[0] + "/" if "/" in parsed.path else "/"
        base = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port and parsed.port not in (80, 443):
            base += f":{parsed.port}"

        chunk_urls = set()
        for pattern in WEBPACK_CHUNK_PATTERNS:
            for match in pattern.finditer(content):
                chunk_ref = match.group(1)
                # If it looks like a full path
                if chunk_ref.endswith(('.js', '.mjs')):
                    if chunk_ref.startswith('/'):
                        chunk_urls.add(base + chunk_ref)
                    elif chunk_ref.startswith('http'):
                        chunk_urls.add(chunk_ref)
                    else:
                        chunk_urls.add(base + base_path + chunk_ref)

        for chunk_url in list(chunk_urls)[:20]:  # Cap per file
            if not in_scope(chunk_url, self.base_reg):
                continue
            try:
                chunk_data = await self._fetch_url(chunk_url, as_text=False)
                if not chunk_data or len(chunk_data) < 20:
                    continue

                sha = hashlib.sha256(chunk_data).hexdigest()
                if sha in self.downloaded_hashes:
                    continue
                self.downloaded_hashes.add(sha)

                fname = safe_filename_from_url(chunk_url, sha, ".js")
                fpath = self.js_dir / fname

                txt = chunk_data.decode("utf-8", errors="ignore") if isinstance(chunk_data, bytes) else chunk_data
                if self.enable_beautify and jsbeautifier:
                    txt = jsbeautifier.beautify(txt)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(txt)

                self.js_files.append({
                    "url": chunk_url,
                    "filepath": str(fpath),
                    "filename": fpath.name,
                    "size": len(chunk_data),
                    "sha256": sha,
                    "source_page": js_url,
                })
                self.logger.debug(f"Webpack chunk saved: {fname}")
            except Exception as e:
                self.logger.debug(f"Webpack chunk fetch failed {chunk_url}: {e}")

    async def _save_inline_scripts(self, page, source_url: str):
        try:
            codes = await page.eval_on_selector_all("script:not([src])", "els => els.map(e => e.textContent || '')")
        except Exception:
            codes = []

        for code in codes:
            code = (code or "").strip()
            if not code:
                continue
            sha = hashlib.sha256(code.encode("utf-8", errors="ignore")).hexdigest()
            if sha in self.downloaded_hashes:
                continue
            self.downloaded_hashes.add(sha)

            self.inline_counter += 1
            fname = f"inline_{self.inline_counter}_{sha[:8]}.js"
            fpath = self.js_dir / fname
            try:
                txt = code
                if self.enable_beautify and jsbeautifier:
                    txt = jsbeautifier.beautify(code)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(txt)
            except Exception:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(code)

            self.js_files.append({
                "url": f"inline:{source_url}#{self.inline_counter}",
                "filepath": str(fpath),
                "filename": fpath.name,
                "size": len(code.encode("utf-8", errors="ignore")),
                "sha256": sha,
                "source_page": source_url,
            })

    async def _extract_iframe_scripts(self, page, source_url: str):
        """Extract scripts from same-origin iframes."""
        try:
            frames = page.frames
        except Exception:
            return

        for frame in frames:
            try:
                if frame == page.main_frame:
                    continue
                frame_url = frame.url or ""
                if not frame_url or not in_scope(frame_url, self.base_reg):
                    continue

                # Extract inline scripts from iframe
                try:
                    codes = await frame.eval_on_selector_all(
                        "script:not([src])",
                        "els => els.map(e => e.textContent || '')"
                    )
                except Exception:
                    continue

                for code in (codes or []):
                    code = (code or "").strip()
                    if not code:
                        continue
                    sha = hashlib.sha256(code.encode("utf-8", errors="ignore")).hexdigest()
                    if sha in self.downloaded_hashes:
                        continue
                    self.downloaded_hashes.add(sha)

                    self.inline_counter += 1
                    fname = f"iframe_inline_{self.inline_counter}_{sha[:8]}.js"
                    fpath = self.js_dir / fname
                    try:
                        txt = code
                        if self.enable_beautify and jsbeautifier:
                            txt = jsbeautifier.beautify(code)
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.write(txt)
                    except Exception:
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.write(code)

                    self.js_files.append({
                        "url": f"iframe:{frame_url}#{self.inline_counter}",
                        "filepath": str(fpath),
                        "filename": fpath.name,
                        "size": len(code.encode("utf-8", errors="ignore")),
                        "sha256": sha,
                        "source_page": source_url,
                    })
            except Exception:
                continue

    async def _discover_workers(self, page, source_url: str):
        """Discover Web Worker and Service Worker scripts."""
        try:
            worker_urls = await page.evaluate("""() => {
                const urls = [];
                // Find Worker/SharedWorker constructor calls in all scripts
                document.querySelectorAll('script').forEach(s => {
                    const text = s.textContent || '';
                    const workerMatches = text.matchAll(/new\\s+(?:Worker|SharedWorker)\\s*\\(\\s*['"]([^'"]+)['"]/g);
                    for (const m of workerMatches) urls.push(m[1]);
                });
                // Check service worker registration
                if (navigator.serviceWorker && navigator.serviceWorker.controller) {
                    urls.push(navigator.serviceWorker.controller.scriptURL);
                }
                return urls;
            }""")
            for wurl in (worker_urls or []):
                if not wurl:
                    continue
                full_url = urljoin(source_url, wurl)
                if in_scope(full_url, self.base_reg):
                    data = await self._fetch_url(full_url, as_text=False)
                    if data and len(data) > 10:
                        sha = hashlib.sha256(data).hexdigest()
                        if sha in self.downloaded_hashes:
                            continue
                        self.downloaded_hashes.add(sha)
                        fname = safe_filename_from_url(full_url, sha, ".js")
                        fpath = self.js_dir / fname
                        txt = data.decode("utf-8", errors="ignore")
                        if self.enable_beautify and jsbeautifier:
                            try:
                                txt = jsbeautifier.beautify(txt)
                            except Exception:
                                pass
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.write(txt)
                        self.js_files.append({
                            "url": full_url,
                            "filepath": str(fpath),
                            "filename": fpath.name,
                            "size": len(data),
                            "sha256": sha,
                            "source_page": source_url,
                        })
                        self.logger.debug(f"Worker script saved: {fname}")
        except Exception as e:
            self.logger.debug(f"Worker discovery failed: {e}")

    async def _extract_links(self, page, base: str):
        links = set()
        try:
            hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
            actions = await page.eval_on_selector_all("form[action]", "els => els.map(e => e.getAttribute('action'))")
            for a in actions:
                if not a:
                    continue
                links.add(urljoin(base, a))
            for h in hrefs:
                if not h:
                    continue
                u = urljoin(base, h)
                if u.startswith("http"):
                    host = (urlparse(u).hostname or "").lower()
                    if host in VENDOR_HOST_DENY:
                        continue
                    # Preserve hash routes for SPAs (e.g. /#/admin)
                    # but strip plain anchors (e.g. #section)
                    parsed = urlparse(u)
                    if parsed.fragment and parsed.fragment.startswith('/'):
                        links.add(u)  # Keep SPA hash route
                    else:
                        links.add(u.split('#')[0])  # Strip plain anchors
        except Exception:
            pass
        return [u for u in links if in_scope(u, self.base_reg)]

    async def _extract_script_tags(self, page, base: str):
        urls = set()
        try:
            srcs = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.getAttribute('src'))")
            for s in srcs:
                if not s:
                    continue
                u = urljoin(base, s)
                if u.startswith("http"):
                    host = (urlparse(u).hostname or "").lower()
                    if host in VENDOR_HOST_DENY:
                        continue
                    urls.add(u.split('#')[0])
        except Exception:
            pass
        return [u for u in urls if in_scope(u, self.base_reg)]
