"""Shared scrapy spider logic for the two Abans (buyabans.com) crawlers.

Both the mobile-phone and laptop spiders use identical listing pages and
detail-page markup; only the base URL, the pagination element position and
the "product_data" selector differ.  This module holds that shared flow so
the concrete spiders stay declarative and cannot drift apart in their
error handling.
"""

import scrapy

from ..items import AbansLkItem
from ..abans_common import (
    extract_price,
    extract_text,
    parse_max_page,
    should_continue_pagination,
)

# Selectors shared by both buyabans.com page templates.
DETAIL_LINKS_SELECTOR = '#filter-container > li > a::attr(href)'
OLD_PRICE_SELECTOR = (
    '#product > div.primary-box.row.all-details > '
    'div.pb-right-column.col-xs-12.col-md-5.col-sm-12.detail-con-padding > '
    'div.row > div > div.old-price::text'
)


class BaseAbansSpider(scrapy.Spider):
    #: Overridden by subclasses.
    base_url = None
    #: Pagination "last page" link selector; differs between the two pages.
    max_page_selector = None
    #: Selector for the descriptive product_data field.
    product_data_selector = None

    page_number = 2

    def parse(self, response):
        detail_links = response.css(DETAIL_LINKS_SELECTOR).getall() or []

        for link in detail_links:
            yield scrapy.Request(
                response.urljoin(link),
                callback=self._parse_detail,
            )

        max_pagenumber = parse_max_page(
            extract_text(response.css(self.max_page_selector))
        )

        if should_continue_pagination(
            self.page_number, max_pagenumber, detail_links
        ):
            next_page = '{}?page={}'.format(
                self.base_url, self.page_number
            )
            self.page_number += 1
            yield response.follow(next_page, callback=self.parse)

    def _parse_detail(self, response):
        # Every field is extracted defensively: a missing element yields
        # None (an empty value in the item) rather than an exception that
        # would drop the whole product.
        new_product = AbansLkItem()
        new_product['product_name'] = extract_text(
            response.css('.product-name::text')
        )
        new_product['product_price'] = extract_price(
            response.css('#item_price::text')
        )
        new_product['product_model'] = extract_text(
            response.css('.modal-no::text')
        )
        new_product['product_data'] = extract_text(
            response.css(self.product_data_selector)
        )
        new_product['product_old_price'] = extract_price(
            response.css(OLD_PRICE_SELECTOR)
        )
        new_product['product_image'] = extract_text(
            response.css('#zoom_03::attr(src)')
        )
        request = getattr(response, 'request', None)
        new_product['detailed_url'] = (
            request.url if request is not None else response.url
        )

        yield new_product
