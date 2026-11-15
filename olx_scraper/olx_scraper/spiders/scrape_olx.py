# -*- coding: utf-8 -*-
"""OLX spider.

The crawl is intentionally defensive: a single malformed advertisement never
aborts the whole run.  Each advertisement is processed independently and the
crawl outcome (found / skipped / failed) is recorded on
:class:`olx_scraper.outcome.CrawlOutcome`.

Failure semantics
-----------------
* list page valid and contains ads        -> ads are scraped
* list page valid but has no ads          -> success, ``list_page_empty=True``
* list page structure not understood      -> crawl fails with
  ``failure_reason="list_page_invalid"`` and Scrapy's finish reason is set
  accordingly (the crawl does **not** silently report an empty success)
* price text cannot be parsed             -> that ad is skipped and counted
* detail page lacks phone / description   -> explicit fallback values are used
  and the corresponding ``*_missing`` flag is set
"""

from __future__ import annotations

import re

from scrapy import Request
from scrapy.exceptions import CloseSpider
from scrapy.http import Response
from scrapy.spiders import Spider

from olx_scraper.config import load_info
from olx_scraper.items import OlxScraperItem
from olx_scraper.outcome import (
    SKIP_DETAIL_REQUEST_FAILED,
    SKIP_MISSING_DETAIL_URL,
    SKIP_PRICE_UNPARSEABLE,
    CrawlOutcome,
)
from olx_scraper.parsing import (
    LIST_AD_XPATH,
    LIST_EMPTY,
    LIST_INVALID,
    detect_list_page_state,
    normalize_price_text,
    parse_price,
)

# Explicit downgrade values used when a detail page lacks information.
PHONE_FALLBACK = "Not available"
DESCRIPTION_FALLBACK = "Not available"
SELLER_FALLBACK = "Not available"
DATE_FALLBACK = "Not available"

_PHONE_CHARS_RE = re.compile(r"[^\d+]")
_LIST_BLOCKED_REASON = "list_page_invalid"


def num2month(num):
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    if 0 <= num <= 12:
        return months[num - 1]
    return -1


def month2num(month):
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    if month in months:
        return months.index(month) + 1
    return -1


def _first_text(node, xpath, default=""):
    """First non-empty stripped text matched by *xpath* below *node*."""
    values = node.xpath(xpath).getall()
    for value in values:
        if value is None:
            continue
        cleaned = " ".join(value.split())
        if cleaned:
            return cleaned
    return default


def normalize_phone(value):
    """Return a Pakistani style phone number or ``None`` when unusable."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    digits = _PHONE_CHARS_RE.sub("", text)
    digit_count = len(digits.replace("+", ""))
    if digit_count < 7:
        return None
    if digits.startswith("+"):
        return digits
    if digits.startswith("0"):
        return "+92" + digits[1:]
    return "+92" + digits


class ScraperSpider(Spider):
    name = "scrape_olx"
    start_urls = ["https://www.olx.com/"]

    def __init__(self, *args, **kwargs):
        # ``base_url`` lets a caller point the crawl at a local/staging site
        # (``scrapy crawl scrape_olx -a base_url=http://localhost:8080/``)
        # without changing args.py.  The default stays the real OLX site.
        base_url = kwargs.pop("base_url", None)
        super().__init__(*args, **kwargs)
        if base_url:
            self.start_urls = [base_url]
        self.outcome = CrawlOutcome()
        self.min_price = 0
        self._info = load_info()
        self.d = None

    # ------------------------------------------------------------------ setup
    def _configure(self):
        if self.d is not None:
            return
        from datetime import date

        self.d = date.today()
        min_p = self._info("minimumprice")
        try:
            self.min_price = int(min_p) if min_p not in ("", None) else 0
        except (TypeError, ValueError):
            self.logger.warning(
                "Ignoring invalid minimumprice=%r; no minimum price filter used.",
                min_p,
            )
            self.min_price = 0

    def _list_url(self, base_url):
        city = self._info("city") or ""
        product = self._info("product") or ""
        city_slug = city.replace(" ", "").lower()
        product_slug = product.replace(" ", "-").lower()
        return "%s%s/q-%s" % (base_url.rstrip("/") + "/", city_slug, product_slug)

    # ----------------------------------------------------------- spiders hooks
    async def start(self):
        self._configure()
        for url in self.start_urls:
            yield Request(url, callback=self.parse, errback=self.list_request_failed)

    # Kept for Scrapy < 2.13 compatibility and for direct use in tests.
    def start_requests(self):
        self._configure()
        for url in self.start_urls:
            yield Request(url, callback=self.parse, errback=self.list_request_failed)

    # ------------------------------------------------------------ list page
    def parse(self, response):
        self._configure()
        url = self._list_url(response.url)
        yield Request(
            url,
            callback=self.parse_products,
            errback=self.list_request_failed,
            dont_filter=True,
        )

    def parse_products(self, response):
        self._configure()
        state = detect_list_page_state(response)

        if state == LIST_INVALID:
            message = "List page structure not recognised (url=%s)" % response.url
            self.outcome.fail(_LIST_BLOCKED_REASON, message)
            self._push_stats()
            self.logger.error(message)
            # Explicit failure signal: the crawl must not end as an empty
            # success.  CloseSpider makes Scrapy report this finish reason.
            raise CloseSpider(reason=_LIST_BLOCKED_REASON)

        if state == LIST_EMPTY:
            self.outcome.record_empty_list_page()
            self._push_stats()
            self.logger.info(
                "List page contains no advertisements (url=%s); "
                "reporting a successful zero-result crawl.",
                response.url,
            )
            return

        for ad in response.xpath(LIST_AD_XPATH):
            self.outcome.record_ad_seen()
            try:
                yielded = self._handle_list_ad(response, ad)
            except Exception:
                self.logger.exception("Unexpected error while parsing a list ad")
                self.outcome.record_skip("list_ad_parse_error")
                continue
            if yielded is not None:
                yield yielded

        self._push_stats()

    def _handle_list_ad(self, response, ad):
        abs_url = ad.xpath(".//h3/a/@href").get() or ad.xpath(".//a/@href").get()
        if not abs_url:
            self.logger.warning("Skipping advertisement without detail link")
            self.outcome.record_skip(SKIP_MISSING_DETAIL_URL)
            return None
        abs_url = response.urljoin(abs_url.strip())

        title = _first_text(ad, ".//h3/a/span/text()") or _first_text(
            ad, ".//h3/a//text()"
        )
        date = _first_text(
            ad, ".//*[@class='color-9 lheight14 margintop3 small']/text()"
        )
        raw_price = _first_text(ad, ".//p[contains(@class,'price')]/strong/text()")
        if not raw_price:
            raw_price = _first_text(ad, ".//*[contains(@class,'price')]//text()")

        price_value = parse_price(raw_price)
        if price_value is None:
            self.logger.warning(
                "Skipping advertisement with unparseable price %r (url=%s)",
                raw_price,
                abs_url,
            )
            self.outcome.record_skip(SKIP_PRICE_UNPARSEABLE)
            return None

        if self.min_price and price_value < self.min_price:
            self.outcome.record_filtered()
            return None

        return Request(
            abs_url,
            callback=self.product_page,
            errback=self.detail_request_failed,
            meta={
                "url": abs_url,
                "title": title,
                "price": normalize_price_text(raw_price),
                "price_value": price_value,
                "date": date,
            },
        )

    # ------------------------------------------------------------ detail page
    def product_page(self, response: Response):
        meta = response.meta
        try:
            item = self._build_item(response, meta)
        except Exception:
            # Parsing a detail page must never lose the rest of the crawl.
            self.logger.exception(
                "Unexpected error while parsing detail page %s", response.url
            )
            self.outcome.record_skip("detail_parse_error")
            self._push_stats()
            return

        self.outcome.record_ad_yielded()
        self._push_stats()
        yield item

    def _build_item(self, response, meta):
        user = _first_text(
            response, '//*[contains(@class,"userdetails")]/span/text()'
        )
        seller_missing = not user
        if seller_missing:
            user = SELLER_FALLBACK

        phone_missing = False
        phone_raw = None
        contact_values = response.xpath(
            '//*[contains(@class,"contactitem")]/strong/text()'
        ).getall()
        for candidate in contact_values:
            phone_raw = normalize_phone(candidate)
            if phone_raw:
                break
        if not phone_raw:
            phone_missing = True
            phone_raw = PHONE_FALLBACK

        description = _first_text(response, "//*[@id='textContent']/p/text()")
        if not description:
            description = _first_text(response, "//*[@id='textContent']//text()")
        description_missing = not description
        if description_missing:
            description = DESCRIPTION_FALLBACK

        date = meta.get("date") or DATE_FALLBACK
        date_missing = not meta.get("date")

        if seller_missing:
            self.logger.info("Seller missing on %s; stored fallback.", response.url)
        if phone_missing:
            self.outcome.record_missing_phone()
            self.logger.info("Phone missing on %s; stored fallback.", response.url)
        if description_missing:
            self.outcome.record_missing_description()
            self.logger.info(
                "Description missing on %s; stored fallback.", response.url
            )

        return OlxScraperItem(
            title=meta.get("title", ""),
            phone_number=phone_raw,
            date=date,
            price=meta.get("price", ""),
            price_value=meta.get("price_value"),
            url=meta.get("url", response.url),
            description=description,
            user=user,
            phone_missing=phone_missing,
            description_missing=description_missing,
            seller_missing=seller_missing,
            date_missing=date_missing,
        )

    # ---------------------------------------------------------------- errbacks
    def list_request_failed(self, failure):
        message = "List page request failed: %s" % (failure.value,)
        self.outcome.fail("list_request_failed", message)
        self._push_stats()
        self.logger.error(message)
        raise CloseSpider(reason="list_request_failed")

    def detail_request_failed(self, failure):
        request = getattr(failure, "request", None)
        url = getattr(request, "url", None) or "<unknown>"
        self.logger.warning(
            "Skipping advertisement whose detail page failed (%s): %s",
            url,
            failure.value,
        )
        self.outcome.record_skip(SKIP_DETAIL_REQUEST_FAILED)
        self._push_stats()

    # ------------------------------------------------------------- reporting
    def _push_stats(self):
        stats = getattr(self, "crawler", None)
        stats = getattr(stats, "stats", None)
        if stats is None:
            return
        try:
            for key, value in self.outcome.as_stats().items():
                stats.set_value(key, value)
        except Exception:  # pragma: no cover - stats must never break a crawl
            pass

    def closed(self, reason):
        """Leave a final, checkable record for every crawl."""
        self.outcome.record_finish_reason(reason)
        if not self.outcome.pipeline_active and self.outcome.ads_yielded:
            # Items were produced but no pipeline consumed them.
            self.outcome.record_pipeline_disabled()
        snap = self.outcome.snapshot()
        self._push_stats()
        if self.outcome.failed:
            self.logger.error("OLX crawl finished with failure: %s", snap)
        else:
            self.logger.info("OLX crawl finished: %s", snap)
        return None


__all__ = [
    "DATE_FALLBACK",
    "DESCRIPTION_FALLBACK",
    "PHONE_FALLBACK",
    "SELLER_FALLBACK",
    "ScraperSpider",
    "month2num",
    "normalize_phone",
    "num2month",
]
