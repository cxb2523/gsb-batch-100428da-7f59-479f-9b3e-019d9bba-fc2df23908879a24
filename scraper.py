"""
scraper.py — Pure DrissionPage Scraper
"""

from bs4 import BeautifulSoup, Comment
from DrissionPage import ChromiumPage, ChromiumOptions
import tempfile
import shutil
import random
import time
import os
import traceback
import json
import urllib.request

MAX_HTML_CHARS = 300000

# Data-source markers reported back to callers via ScrapeResult.source
SOURCE_BROWSER = "browser"   # rendered by the headless browser
SOURCE_STATIC = "static"     # degraded: plain HTTP fetch, no browser
SOURCE_FAILED = "failed"     # nothing could be retrieved

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


class ScrapeResult(tuple):
    """Backward-compatible (html, api_data) tuple carrying a `source` marker.

    Old callers can keep unpacking it as a 2-tuple:
        html, api_data = get_website_content(...)
    New callers may inspect `.source` ("browser" | "static" | "failed").
    """

    def __new__(cls, html, api_data, source=SOURCE_BROWSER):
        obj = super().__new__(cls, (html, api_data))
        obj.source = source
        return obj

    def __repr__(self):
        return f"ScrapeResult(html={'None' if self[0] is None else f'<{len(self[0])} chars>'}, api_data=<{len(self[1])} chars>, source={self.source!r})"


def _clean_html(raw_html: str) -> str:
    """Strip script/style/svg/iframe/noscript tags and comments, collapse whitespace."""
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "svg", "iframe", "noscript"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    return " ".join(str(soup.body or soup).split())


def _static_fetch(url: str, timeout: int = 30) -> str | None:
    """Fetch raw page HTML over plain HTTP — no browser required."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except Exception as e:
        print(f"⚠️ Static fallback fetch failed: {e}")
        return None


def _browser_unavailable_result(url: str, extraction_mode: str) -> ScrapeResult:
    """Degraded path when the browser cannot start at all.

    - html mode: fall back to a static HTTP fetch through the same cleaning pipeline.
    - network mode: interception requires a live browser — fail explicitly,
      never fabricate intercepted data.
    """
    if extraction_mode == "network":
        print("❌ Network mode requires the browser for interception; no fallback available.")
        return ScrapeResult(None, "", SOURCE_FAILED)

    print("🔄 Falling back to static HTTP fetch (no browser)...")
    raw_html = _static_fetch(url)
    if not raw_html:
        print("❌ Static fallback also failed.")
        return ScrapeResult(None, "", SOURCE_FAILED)

    clean = _clean_html(raw_html)[:MAX_HTML_CHARS]
    print(f"✅ Static fallback captured {len(clean):,} chars HTML")
    return ScrapeResult(clean, "", SOURCE_STATIC)


def get_website_content(url: str, headless: bool = False, extraction_mode: str = "html") -> tuple[str | None, str]:
    print(f"\n🕵️ Scraping (Pure DrissionPage): {url}")
    
    is_server = bool(os.environ.get("RENDER") or os.environ.get("CHROMIUM_PATH"))

    temp_user_data = tempfile.mkdtemp()
    
    co = ChromiumOptions()
    co.headless(headless or is_server)
    co.set_argument('--headless=new')
    if is_server:
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        co.set_argument('--disable-software-rasterizer')
    co.set_user_agent(USER_AGENT)
    co.set_argument(f'--user-data-dir={temp_user_data}')
    co.set_argument('--window-size=1280,720')
    
    if is_server:
        co.set_argument('--disable-extensions')
        co.set_argument('--js-flags=--max-old-space-size=256')
        co.set_argument('--single-process')
        # Fix DrissionPage WebSocket issue in Docker:
        co.set_argument('--remote-debugging-address=0.0.0.0')
        debug_port = random.randint(9200, 9300)
        co.set_argument(f'--remote-debugging-port={debug_port}')
        co.set_local_port(debug_port)
    else:
        co.auto_port()

    chromium_path = os.environ.get("CHROMIUM_PATH")
    if chromium_path:
        co.set_browser_path(chromium_path)
    
    page = None
    api_responses = []
    total_api_bytes = 0
    MAX_API_DATA_BYTES = 50_000
    
    try:
        # Retry logic for Docker cold starts
        max_retries = 3 if is_server else 1
        for attempt in range(max_retries):
            try:
                page = ChromiumPage(addr_or_opts=co)
                break
            except Exception as e:
                print(f"⚠️ Browser attempt {attempt+1} failed: {e}")
                if attempt == max_retries - 1:
                    print("❌ Browser connection failed completely.")
                    return _browser_unavailable_result(url, extraction_mode)
                time.sleep(2)
                
        page.run_js("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # Only use listener locally to avoid Docker instability
        use_listener = not is_server
        
        if extraction_mode == "network":
            print("[Scraper] Mode: Network Intercept Fast Path")
            try:
                page.listen.start(targets='api|graphql|json|v1|v2|query|search|feed')
            except:
                pass
        
        page.set.timeouts(page_load=30, script=10)
        
        print(f"🌐 Loading {url}...")
        try:
            page.get(url)
            page.wait.doc_loaded()  # Wait for document.readyState
        except Exception as e:
            print(f"⚠️ Page load warning: {e}")
            
        if extraction_mode == "network":
            print("⏳ Waiting for API responses (up to 8s)...")
            try:
                packet = page.listen.wait(timeout=8)
                if packet and packet.response and packet.response.body:
                    try:
                        data = json.loads(packet.response.body)
                        api_responses.append({"url": packet.url[:200], "data": data})
                        total_api_bytes += len(packet.response.body)
                    except:
                        pass
            except Exception as e:
                print(f"⚠️ Listen wait timeout or err: {e}")
            try:
                page.listen.stop()
            except:
                pass
        else:
            print("⏳ Strict DOM Wait: Waiting for dynamic elements...")
            try:
                # Wait for article, main, or dynamically injected divs to appear
                page.ele('css:article, main, div[id*="root"], div[id*="app"]', timeout=10)
            except:
                time.sleep(3)  # Fallback for dynamic content mapping
            
            # Scroll
            for _ in range(4):
                page.scroll.down(500)
                time.sleep(0.5)
                
            if use_listener:
                # Fallback intercept logic from before for regular mode
                try:
                    for packet in page.listen.steps(timeout=2):
                        if not packet.response: continue
                        body = packet.response.body
                        if not body or len(body) < 20: continue
                        try:
                            data = json.loads(body)
                            api_responses.append({"url": packet.url[:200], "data": data})
                            total_api_bytes += len(body)
                            if total_api_bytes > MAX_API_DATA_BYTES: break
                        except:
                            pass
                except:
                    pass
                try:
                    page.listen.stop()
                except:
                    pass
                
        raw_html = page.html
        clean = _clean_html(raw_html)

        api_data_str = ""
        if api_responses:
            api_data_str = json.dumps(api_responses, ensure_ascii=False)[:MAX_API_DATA_BYTES]

        print(f"✅ Captured {len(clean):,} chars HTML + {len(api_data_str):,} chars API")
        return ScrapeResult(clean[:MAX_HTML_CHARS], api_data_str, SOURCE_BROWSER)

    except Exception as e:
        print(f"❌ Scraper error: {e}")
        traceback.print_exc()
        return ScrapeResult(None, "", SOURCE_FAILED)
    finally:
        if page:
            try:
                page.quit()
            except:
                pass
        try:
            shutil.rmtree(temp_user_data, ignore_errors=True)
        except:
            pass