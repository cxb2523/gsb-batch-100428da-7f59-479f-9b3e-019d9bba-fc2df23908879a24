import os
import re
import json
import time
import html
import urllib.parse
import requests
from bs4 import BeautifulSoup
from singleProductDetail import parse_product_details

"""
Google Shopping (udm=28) Consistent Scraper via scrape.do

What this does
--------------
1) Run a single product extraction pass using async pagination
2) Collect all unique products found across pages
3) Extract basic info (title, price, image, seller, rating, reviews) from cards
4) For products with detail parameters, fetch extended info via the OAPV endpoint
5) Export JSON with all collected data

Usage
-----
export SCRAPE_DO_TOKEN=<your_token>
export GOOGLE_QUERY="your search query"
python scraper_consistent.py

Environment Variables
--------------------
- SCRAPE_DO_TOKEN: Your scrape.do API token (required)
- GOOGLE_QUERY: Search query (default: "pc wireless gaming headset")
- OUT_JSON: Output file name (default: "google_shopping_results.json")
- MAX_PAGES: Maximum pages to scrape (default: 0 = unlimited)
- PAUSE_SECONDS: Delay between requests, also used between retries (default: 1.0)
- MAX_RETRIES: Retries per failed request after the first attempt (default: 2,
  so up to 3 attempts total). Pages that still fail are recorded with their
  start offset and error, and are NOT counted as empty pages.
"""

# -----------------------------
# Stop reasons (bilingual, human-readable)
# -----------------------------
STOP_REASON_NO_NEW_PRODUCTS = "no_new_products"
STOP_REASON_INTERRUPTED = "interrupted_by_failed_pages"

STOP_REASON_TEXT = {
    STOP_REASON_NO_NEW_PRODUCTS: (
        "无新产品：成功请求确认页面已无商品，采集正常结束 "
        "(no new products: confirmed by successfully fetched empty pages)"
    ),
    STOP_REASON_INTERRUPTED: (
        "因失败页中断：部分页面在有限次重试后仍请求失败，结果可能被网络错误截断 "
        "(interrupted by failed pages: requests kept failing after retries, "
        "results may be truncated)"
    ),
}

# -----------------------------
# Config
# -----------------------------
SCRAPE_DO_TOKEN = os.getenv("SCRAPE_DO_TOKEN", "<your_token>")
QUERY = os.getenv("GOOGLE_QUERY", "pc wireless gaming headset")
SEARCH_URL = f"https://www.google.com/search?q={urllib.parse.quote_plus(QUERY)}&udm=28"

OUT_JSON = os.getenv("OUT_JSON", "google_shopping_results.json")

# NOTE: Smaller PAGE_SIZE (for example 1) increases the number of requests sent
# but can sometimes surface more total products, because each async page loads
# a slightly different slice of results.
PAGE_SIZE       = int(os.getenv("PAGE_SIZE", "10"))
MAX_PAGES       = int(os.getenv("MAX_PAGES", "0"))  # 0 = unlimited
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
PAUSE_SECONDS   = float(os.getenv("PAUSE_SECONDS", "1.0"))
# Number of retries after the first failed attempt. Kept small on purpose so a
# flaky proxy is retried but a dead page does not stall the whole run.
MAX_RETRIES     = int(os.getenv("MAX_RETRIES", "2"))
# Stop a pass only after this many CONSECUTIVE pages where the request
# succeeded but the page genuinely contained no products. Failed pages never
# contribute to this counter.
EMPTY_PAGE_LIMIT = 3

# -----------------------------
# HTTP helper
# -----------------------------
def scrape_do(url: str, session: requests.Session) -> requests.Response:
    if not SCRAPE_DO_TOKEN or SCRAPE_DO_TOKEN.startswith("<PUT_"):
        raise SystemExit("Set SCRAPE_DO_TOKEN env var (or edit the script).")
    api = f"http://api.scrape.do/?token={SCRAPE_DO_TOKEN}&url={urllib.parse.quote(url)}"
    r = session.get(api)
    r.raise_for_status()
    return r


def request_with_retries(label: str, url: str, session: requests.Session, fetch):
    """
    Call ``fetch(url, session)`` with a bounded number of retries.

    The first attempt plus up to MAX_RETRIES retries are made, waiting
    PAUSE_SECONDS between attempts. The Scrape.do call itself is unchanged;
    this only adds the retry loop around it.

    Returns:
        (result, None) on success
        (None, last_error) once every attempt has failed
    """
    last_error = None
    max_attempts = MAX_RETRIES + 1
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch(url, session), None
        except requests.RequestException as e:
            last_error = e
            if attempt < max_attempts:
                print(
                    f"[retry] {label} attempt {attempt}/{max_attempts} failed: {e}; "
                    f"retrying in {PAUSE_SECONDS:g}s"
                )
                time.sleep(PAUSE_SECONDS)
            else:
                print(f"[retry] {label} failed after {max_attempts} attempts: {e}")
    return None, last_error

# -----------------------------
# Parsing helpers
# -----------------------------
def extract_product_data(soup: BeautifulSoup, ei=None):
    """
    Parse products from udm=28 layout.
    Returns (list of products, total_found_count).
    """
    products = []
    seen_veds = set()
    total_found = 0

    for group in soup.select(".MjjYud"):
        for card in group.select(".Ez5pwe"):
            clickable_area = card.select_one('div[data-ved]')
            if not clickable_area:
                continue

            ved = clickable_area.get('data-ved')
            if not ved or ved in seen_veds:
                continue
            seen_veds.add(ved)

            title_tag = card.select_one(".gkQHve.SsM98d.RmEs5b")
            title = title_tag.get_text(strip=True) if title_tag else None

            price_tag = card.select_one(".lmQWe")
            price = price_tag.get_text(strip=True) if price_tag else None

            if not title or not price:
                continue
            
            total_found += 1

            image_url = "N/A"
            img = card.select_one("img.VeBrne")
            if not img:
                img = card.select_one("img.nGT6qb")
            if img:
                for attr in ("src", "data-src", "data-jslayout-progressive-load"):
                    if img.has_attr(attr):
                        attr_value = img[attr]
                        if (not attr_value.startswith("data:image") and
                            (attr_value.startswith("http") or attr_value.startswith("//"))):
                            image_url = attr_value
                            break

            # Extract seller name
            seller_name = "N/A"
            seller_span = card.select_one("span.WJMUdc.rw5ecc")
            if seller_span:
                seller_name = seller_span.get_text(strip=True)

            # Extract rating
            rating = None
            rating_span = card.select_one("span.yi40Hd.YrbPuc")
            if rating_span:
                rating = rating_span.get_text(strip=True)

            # Extract review count
            review_count = None
            review_span = card.select_one("span.RDApEe.YrbPuc")
            if review_span and review_span.name == "span":
                review_text = review_span.get_text(strip=True)
                if review_text and len(review_text) < 20:
                    review_match = re.search(r'\(([0-9.]+[KM]?)\)', review_text)
                    if review_match:
                        review_count = review_match.group(1)
                    elif re.match(r'^[0-9.]+[KM]?$', review_text):
                        review_count = review_text
            
            link = "N/A"
            
            # Try to extract product detail parameters from data attributes.
            # Google may or may not embed these; when present they enable
            # fetching richer detail via the OAPV endpoint.
            product_params = None
            product_container = (
                card.select_one('[data-cid]') or
                card.select_one('.MtXiu') or
                card.select_one('.shntl') or
                card.select_one('.sh-dgr__content')
            )
            if not product_container and card.get('data-cid'):
                product_container = card

            if product_container:
                catalogid = product_container.get('data-cid')
                gpcid = product_container.get('data-gid')
                headline_offer_docid = product_container.get('data-oid')
                image_docid = product_container.get('data-iid')
                mid = product_container.get('data-mid')
                rds = product_container.get('data-rds')
                
                if all([catalogid, gpcid, headline_offer_docid, image_docid, mid]):
                    product_params = {
                        "catalogid": catalogid,
                        "gpcid": gpcid,
                        "headlineOfferDocid": headline_offer_docid,
                        "imageDocid": image_docid,
                        "mid": mid,
                        "rds": rds or "",
                        "ei": ei,
                        "ved": ved
                    }

            products.append({
                "title": title,
                "price": price,
                "link": link,
                "image_url": image_url,
                "seller_name": seller_name,
                "rating": rating,
                "review_count": review_count,
                "product_params": product_params
            })
            
    return products, total_found

def extract_tokens_from_initial(html_text: str, soup: BeautifulSoup):
    """Extract tokens needed for async pagination."""
    # ei / kEI
    ei = None
    m = re.search(r"_g=\{kEI:'([^']+)'", html_text)
    if not m:
        m = re.search(r'kEI[:=]\s*[\'"]([^\'"]+)[\'"]', html_text)
    if m:
        ei = m.group(1)

    basejs = basecss = basecomb = None
    for script in soup.find_all("script"):
        text = script.string or ""
        if not text:
            continue
        if "google.xjs" in text:
            obj = re.search(r"google\.xjs\s*=\s*(\{.*?\})", text, re.DOTALL)
            if obj:
                body = obj.group(1)
                pairs = re.findall(r"(\w+)\s*:\s*['\"]([^'\"]+)['\"]", body)
                if pairs:
                    d = {}
                    for k, v in pairs:
                        v = v.replace("\\x3d", "=").replace("\\x22", '"').replace("\\x26", "&")
                        d[k] = v
                    basejs   = d.get("basejs", basejs)
                    basecss  = d.get("basecss", basecss)
                    basecomb = d.get("basecomb", basecomb)
    return ei, basejs, basecss, basecomb

def build_async_url(ei, basejs, basecss, basecomb, q, start):
    """Build URL for async pagination endpoint."""
    arc_id = f"srp_{ei or 'X'}_{start}"
    _id    = f"arc-srp_{ei or 'X'}_{start}"

    async_parts = [
        f"arc_id:{arc_id}",
        "ffilt:all",
        "ve_name:MoreResultsContainer",
        "use_ac:false",
        "inf:1",
        "_pms:s",
        "_fmt:pc",
        f"_id:{_id}",
    ]
    if basejs:   async_parts.append(f"_basejs:{basejs}")
    if basecss:  async_parts.append(f"_basecss:{basecss}")
    if basecomb: async_parts.append(f"_basecomb:{basecomb}")
    async_param = ",".join(async_parts)

    params = {
        "q": q,
        "udm": "28",
        "start": str(start),
        "sa": "N",
        "asearch": "arc",
        "cs": "1",
        "async": async_param,
    }
    return "https://www.google.com/search?" + urllib.parse.urlencode(params, safe=":,_")

def _unescape_google_inline(html_fragment: str) -> str:
    """De-escape Google's inline HTML encoding."""
    def repl(m):
        return chr(int(m.group(1), 16))
    s = re.sub(r"\\x([0-9a-fA-F]{2})", repl, html_fragment)
    return html.unescape(s)

def fetch_async_snippets(session: requests.Session, async_url: str):
    """Fetch and parse async pagination response."""
    resp = scrape_do(async_url, session)
    body = resp.text
    txt = body.lstrip(")]}'\n ")
    snippets = []

    # Try JSON lines format
    json_hits = re.findall(r'^\{.*?"html":.*\}$', txt, flags=re.M | re.S)
    if json_hits:
        for hit in json_hits:
            try:
                data = json.loads(hit)
                h = data.get("html") or ""
                if h:
                    snippets.append(_unescape_google_inline(h))
            except Exception:
                pass

    # Fallback to raw HTML
    if not snippets:
        snippets.append(txt)

    return snippets

def parse_async_batch(snippets, ei=None):
    products = []
    total_found = 0
    for snip in snippets:
        soup = BeautifulSoup(snip, "html.parser")
        batch_products, batch_total = extract_product_data(soup, ei)
        products.extend(batch_products)
        total_found += batch_total
    return products, total_found


def _fetch_async_with_retry(async_url, session):
    """Retry wrapper around the async pagination endpoint."""
    return request_with_retries("async page", async_url, session, fetch_async_snippets)


def _fetch_plain_with_retry(plain_url, session, ei):
    """Retry wrapper around the plain (non-async) fallback page."""
    def _fetch(url, sess):
        resp = scrape_do(url, sess)
        return extract_product_data(BeautifulSoup(resp.content, "html.parser"), ei)

    return request_with_retries("fallback plain page", plain_url, session, _fetch)


def fetch_result_page(session, ei, basejs, basecss, basecomb, start):
    """
    Fetch one result page and classify the outcome explicitly.

    Three outcomes are returned as the ``status`` field so callers never
    confuse a network failure with a genuinely empty page:

    - "ok":     at least one request path succeeded and products were found.
    - "empty":  every attempted request SUCCEEDED, but the page contained no
                products. Safe to count towards the consecutive-empty stop.
    - "failed": one or more request paths failed after all retries. The page
                is recorded (with start offset + error) and must NOT be
                counted as empty.

    Returns:
        dict with keys:
            status, products, page_total, start,
            async_error, fallback_error, note
    """
    result = {
        "status": "failed",
        "products": [],
        "page_total": 0,
        "start": start,
        "async_error": None,
        "fallback_error": None,
        "note": "",
    }

    # --- Path 1: async pagination endpoint, with bounded retries ---
    async_url = build_async_url(ei, basejs, basecss, basecomb, QUERY, start)
    snippets, async_error = _fetch_async_with_retry(async_url, session)

    if async_error is not None:
        result["async_error"] = repr(async_error)

    async_products = []
    async_total = 0
    if snippets:
        async_products, async_total = parse_async_batch(snippets, ei)

    if async_products:
        result.update(status="ok", products=async_products, page_total=async_total)
        return result

    # The async path produced no products. Try the plain page fallback so we
    # can tell "the page is truly empty" apart from "the async endpoint broke".
    plain_url = (
        f"https://www.google.com/search?q={urllib.parse.quote_plus(QUERY)}"
        f"&udm=28&start={start}"
    )
    plain_result, plain_error = _fetch_plain_with_retry(plain_url, session, ei)

    if plain_error is not None:
        result["fallback_error"] = repr(plain_error)

    if plain_result is not None:
        plain_products, plain_total = plain_result
        if plain_products:
            result.update(status="ok", products=plain_products, page_total=plain_total)
            if async_error is not None:
                # Async failed but the fallback saved the page: still surface
                # the async error for visibility, without marking the page failed.
                result["note"] = "async request failed; products recovered via plain fallback"
            elif not snippets:
                result["note"] = "async returned no snippets; products recovered via plain fallback"
            return result

        # Fallback request succeeded but genuinely has no products.
        if async_error is None:
            # Both request paths succeeded and both are empty -> a real empty page.
            result["status"] = "empty"
            return result
        # Async failed after retries; even though the fallback succeeded empty,
        # we never received a successful response for this offset's full slice,
        # so treat it as a failed page rather than a confirmed-empty one.
        result["note"] = "async failed after retries; plain fallback succeeded but had no products"
        return result

    # Fallback itself failed after retries.
    if async_error is None:
        result["note"] = "async succeeded with no products; plain fallback request failed"
    else:
        result["note"] = "both async and plain fallback requests failed after retries"
    return result

def build_product_detail_url(params: dict, query: str):
    """Build URL for product detail popup."""
    if not params:
        return None
    
    required = ['catalogid', 'gpcid', 'headlineOfferDocid', 'imageDocid', 'mid']
    if not all(params.get(key) for key in required):
        return None
    
    async_parts = [
        f"catalogid:{params['catalogid']}",
        f"gpcid:{params['gpcid']}",
        f"headlineOfferDocid:{params['headlineOfferDocid']}",
        f"imageDocid:{params['imageDocid']}",
        f"mid:{params['mid']}",
    ]
    
    if params.get('rds'):
        async_parts.append(f"rds:{params['rds']}")
    
    async_parts.extend([
        "pvo:3",
        "isp:true",
        f"query:{urllib.parse.quote(query)}",
        "gl:0",
        "pvt:hg",
        "_fmt:jspb"
    ])
    
    if params.get('ei'):
        async_parts.append(f"ei:{params['ei']}")
    
    async_param = ",".join(async_parts)
    
    url_params = {
        "udm": "28",
        "yv": "3",
        "currentpv": "1",
        "q": query,
        "async_context": "PV_OPEN",
        "pvorigin": "3",
        "cs": "0",
        "async": async_param,
    }
    
    if params.get('ved'):
        url_params["ved"] = params['ved']
    if params.get('ei'):
        url_params["ei"] = params['ei']
    
    return "https://www.google.com/async/oapv?" + urllib.parse.urlencode(url_params, safe=":,_")



def fetch_product_details(session: requests.Session, params: dict, query: str):
    """Fetch detailed product information."""
    if not params:
        return None
    
    detail_url = build_product_detail_url(params, query)
    if not detail_url:
        return None
    
    try:
        resp = scrape_do(detail_url, session)
        details = parse_product_details(resp.text)
        return details
    except Exception as e:
        print(f"[detail] Error fetching product details: {e}")
        return None

# -----------------------------
# Main
# -----------------------------
def run_extraction(session, existing_products=None):
    """
    Run a single product extraction pass.

    Args:
        session: requests.Session
        existing_products: dict keyed by (title, price) of products already found

    Returns:
        tuple: (new_products_dict, new_count, failed_pages) where
               new_products_dict is dict of products found in this pass,
               new_count is number of new products added, and failed_pages is
               a list of dicts describing pages whose requests failed after
               all retries (each carries the start offset and error info).
    """
    if existing_products is None:
        existing_products = {}

    print("\n[extraction] Starting...")

    # Initial page: only used to extract tokens required for async pagination.
    # We intentionally do NOT parse products from this HTML, to keep all
    # product collection consistent with the async responses.
    r = scrape_do(SEARCH_URL, session)
    soup = BeautifulSoup(r.content, "html.parser")

    # Extract tokens for async pagination
    ei, basejs, basecss, basecomb = extract_tokens_from_initial(r.text, soup)

    # Products found in this pass
    pass_products = {}
    total_all = 0

    # Only pages where the request SUCCEEDED but no products were returned
    # count towards this stop counter. Failed pages never touch it.
    consecutive_empty_pages = 0
    # Pages that could not be fetched even after retries. Recorded with their
    # start offset and the underlying errors so the caller can report them.
    failed_pages = []
    page_limit = MAX_PAGES if MAX_PAGES > 0 else 999

    for page_idx in range(0, page_limit + 1):
        start = PAGE_SIZE * page_idx

        # One call now distinguishes: failed request vs. genuinely empty page.
        page = fetch_result_page(session, ei, basejs, basecss, basecomb, start)
        new_batch = page["products"]
        page_total = page["page_total"]
        status = page["status"]

        if status == "failed":
            # Do not count this as an empty page: record it and move on to the
            # next offset, per the requirement to keep trying later pages.
            entry = {
                "page": page_idx + 1,
                "start": start,
                "async_error": page["async_error"],
                "fallback_error": page["fallback_error"],
                "error": page["async_error"] or page["fallback_error"],
                "note": page["note"],
            }
            failed_pages.append(entry)
            print(
                f"[page {page_idx + 1}] REQUEST FAILED (start={start}); "
                f"not counted as empty. async_error={page['async_error']} "
                f"fallback_error={page['fallback_error']} ({page['note']})"
            )
            time.sleep(PAUSE_SECONDS)
            continue

        # Add all products (with or without detail params)
        page_added = 0
        with_params = 0
        for p in new_batch:
            key = (p["title"], p["price"])
            if key not in existing_products and key not in pass_products:
                pass_products[key] = p
                page_added += 1
                if p.get("product_params"):
                    with_params += 1

        total_all += page_total

        print(f"[page {page_idx + 1}] found {page_total} products, {page_added} new ({with_params} with detail params)")

        # Stopping logic: stop after N consecutive pages where the request
        # SUCCEEDED but the page genuinely returned no products. We look at
        # page_total (not page_added) so pages full of already-seen products
        # still count as non-empty and keep pagination going.
        if page_total == 0:
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= EMPTY_PAGE_LIMIT:
                print(
                    f"[extraction] Stopping after {EMPTY_PAGE_LIMIT} consecutive "
                    f"successfully-fetched empty pages"
                )
                break
        else:
            consecutive_empty_pages = 0

        time.sleep(PAUSE_SECONDS)

    new_count = len(pass_products)
    if failed_pages:
        print(
            f"[extraction] Found {new_count} new products in this pass; "
            f"{len(failed_pages)} page(s) failed and were skipped"
        )
    else:
        print(f"[extraction] Found {new_count} new products in this pass")

    return pass_products, new_count, failed_pages

def main():
    session = requests.Session()

    print(f"[scraper] Starting Google Shopping scraper")
    print(f"[scraper] Query: {QUERY}")
    print(f"[scraper] Max pages: {MAX_PAGES if MAX_PAGES > 0 else 'unlimited'}")
    print(f"[scraper] Max retries per request: {MAX_RETRIES} (up to {MAX_RETRIES + 1} attempts)")

    # Multi-run extraction: keep running until no new products found
    all_products_dict = {}
    run_index = 0
    # Failed pages accumulated across all runs, tagged with the run number so
    # the final report can point at exactly which offset failed when.
    all_failed_pages = []
    stop_reason = None

    while True:
        run_index += 1

        print(f"\n{'='*80}")
        print(f"[scraper] Extraction run #{run_index}")
        print(f"{'='*80}")

        pass_products, new_count, failed_pages = run_extraction(session, all_products_dict)

        # Merge new products into global dict
        for key, product in pass_products.items():
            all_products_dict[key] = product

        for fp in failed_pages:
            fp_with_run = dict(fp)
            fp_with_run["run"] = run_index
            all_failed_pages.append(fp_with_run)

        print(
            f"[scraper] Run #{run_index} complete: {new_count} new products, "
            f"{len(all_products_dict)} total, {len(failed_pages)} failed page(s)"
        )

        # Stop when no new products were found in a run. Distinguish a real
        # end-of-results from truncation: if any page failed in that run, zero
        # new products cannot be trusted as "everything was scraped".
        if new_count == 0:
            if failed_pages:
                stop_reason = STOP_REASON_INTERRUPTED
                print(
                    f"[scraper] No new products in run #{run_index} but {len(failed_pages)} "
                    f"page(s) failed; stopping as interrupted rather than claiming completion"
                )
            else:
                stop_reason = STOP_REASON_NO_NEW_PRODUCTS
                print(f"[scraper] No new products found in run #{run_index}, stopping multi-run extraction")
            break

        time.sleep(PAUSE_SECONDS)

    all_products = list(all_products_dict.values())

    # Honest end-of-run accounting: readable reason plus every failed page.
    print(f"\n[scraper] Extraction summary:")
    print(f"[scraper] Total unique products: {len(all_products)}")
    print(f"[scraper] Total extraction runs: {run_index}")
    print(f"[scraper] Stop reason: {STOP_REASON_TEXT[stop_reason]}")
    print(f"[scraper] Failed pages: {len(all_failed_pages)}")
    if all_failed_pages:
        print("[scraper] Failed page details (start offset + error):")
        for fp in all_failed_pages:
            print(
                f"  - run #{fp.get('run')} page {fp['page']} start={fp['start']} "
                f"async_error={fp['async_error']} fallback_error={fp['fallback_error']}"
                + (f" [{fp['note']}]" if fp.get("note") else "")
            )

    # Count how many have detail params for OAPV fetching
    products_with_params = [p for p in all_products if p.get("product_params")]
    products_without_params = [p for p in all_products if not p.get("product_params")]

    # 4) Fetch detailed information for products that have valid params
    if products_with_params:
        print(f"\n[details] Fetching detailed information for {len(products_with_params)} products with detail params...")
        success_count = 0
        
        for idx, product in enumerate(products_with_params):
            print(f"[details] {idx + 1}/{len(products_with_params)}: {product['title'][:60]}...")
            
            details = fetch_product_details(session, product["product_params"], QUERY)
            
            if details:
                product.update(details)
                success_count += 1
            
            time.sleep(PAUSE_SECONDS / 2)
        
        print(f"[details] Summary: {success_count}/{len(products_with_params)} successful")
    else:
        print(f"\n[details] No products have detail params for OAPV fetching (Google may have changed their HTML structure)")
        print(f"[details] Basic product data (title, price, image, seller, rating, reviews) was still extracted from cards")

    # Ensure uniform schema: every product gets the same set of keys
    # so downstream consumers see a consistent structure.
    detail_defaults = {
        "brand": None,
        "rating": None,
        "review_count": None,
        "description": None,
        "detail_images": [],
        "reviews": [],
        "forums": [],
        "offers": []
    }
    for product in all_products:
        for key, default in detail_defaults.items():
            product.setdefault(key, default)
        product.pop("product_params", None)

    # 5) Export to JSON
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_products, f, ensure_ascii=False, indent=2)

    print(f"\n[scraper] Done! Scraped {len(all_products)} unique products")
    print(f"[scraper] Saved: {OUT_JSON}")
    
    # Print summary
    total_offers = sum(len(p.get("offers", [])) for p in all_products)
    total_reviews = sum(len(p.get("reviews", [])) for p in all_products)
    total_forums = sum(len(p.get("forums", [])) for p in all_products)
    products_with_desc = sum(1 for p in all_products if p.get("description"))
    
    print(f"[scraper] Total offers: {total_offers}")
    print(f"[scraper] Total reviews: {total_reviews}")
    print(f"[scraper] Total forums: {total_forums}")
    print(f"[scraper] Products with descriptions: {products_with_desc}/{len(all_products)}")

    # Structured result for programmatic callers. The JSON file itself keeps
    # the original product-only schema; failure/stop metadata is reported here
    # and via the console, not injected into product records.
    return {
        "products": all_products,
        "stop_reason": stop_reason,
        "stop_reason_text": STOP_REASON_TEXT[stop_reason],
        "total_runs": run_index,
        "failed_pages": all_failed_pages,
        "output_file": OUT_JSON,
    }

if __name__ == "__main__":
    main()
