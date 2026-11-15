"""Offline, HTML-sample-driven tests for the two Abans spiders.

These tests never touch the network: locally constructed HTML snippets are
fed straight into the parsing helpers and spider callbacks to prove that
missing fields and unparseable pagination degrade to partial results
instead of aborting the crawl.

Run (from the repository root):

    python -I -m unittest tests.test_abans_parsing -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsel import Selector

from abans_lk.abans_common import (
    FALLBACK_MAX_PAGE,
    extract_price,
    extract_text,
    parse_max_page,
    should_continue_pagination,
)
from abans_lk.spiders.abans_mobile_spider import AbansSpider as MobileSpider
from abans_lk.spiders.abans_laptop_spider import AbansSpider as LaptopSpider

try:
    from scrapy.http import TextResponse, Request
except ImportError:  # pragma: no cover
    TextResponse = None


OLD_PRICE_MARKUP = (
    '<div id="product"><div class="primary-box row all-details">'
    '<div class="pb-right-column col-xs-12 col-md-5 col-sm-12 '
    'detail-con-padding"><div class="row"><div>'
    '<div class="old-price">%s</div>'
    '</div></div></div></div></div>'
)

FULL_DETAIL_HTML = """
<html><body>
  <h1 class="product-name">
    Test Phone
  </h1>
  <span id="item_price">
    Rs. 29,990
  </span>
  <span class="modal-no">Model No: T-1</span>
  <p class="intro">A short product description.</p>
  <img id="zoom_03" src="https://cdn.example.com/phone.jpg" />
  {old_price}
</body></html>
"""

LIST_HTML = """
<html><body>
  <ul id="filter-container">
    <li><a href="/mobile-phones/test-phone">Test Phone</a></li>
    <li><a href="/mobile-phones/another-phone">Another Phone</a></li>
  </ul>
</body></html>
"""

LIST_HTML_WITH_PAGER = LIST_HTML + """
<div id="columns"><div class="row"><div class="sortPagiBar"><div>
  <nav><ul><ul>
    <li><a>1</a></li>
    <li><a>2</a></li>
    <li><a>3</a></li>
    <li><a>4</a></li>
    <li><a href="#">&gt;</a></li>
  </ul></ul></nav>
</div></div></div></div>
"""

EMPTY_LIST_HTML = '<ul id="filter-container"></ul>'


def make_response(url, html, with_request=True):
    request = Request(url=url) if with_request else None
    return TextResponse(
        url=url,
        encoding='utf-8',
        body=html.encode('utf-8'),
        request=request,
    )


class ExtractTextTests(unittest.TestCase):
    def test_returns_stripped_text(self):
        sel = Selector(text='<h1 class="product-name">\n  Hello  \n</h1>')
        self.assertEqual(extract_text(sel.css('.product-name::text')),
                         'Hello')

    def test_missing_element_returns_none(self):
        sel = Selector(text='<h1>x</h1>')
        self.assertIsNone(extract_text(sel.css('.absent::text')))

    def test_blank_text_returns_none(self):
        sel = Selector(text='<h1 class="p">   </h1>')
        self.assertIsNone(extract_text(sel.css('.p::text')))

    def test_attribute_selector(self):
        sel = Selector(text='<img id="zoom_03" src="img.jpg" />')
        self.assertEqual(
            extract_text(sel.css('#zoom_03::attr(src)')), 'img.jpg'
        )

    def test_none_selector_returns_none(self):
        self.assertIsNone(extract_text(None))


class ExtractPriceTests(unittest.TestCase):
    def test_multiline_single_text_node(self):
        # One node, newline-wrapped, label + price on separate lines.
        sel = Selector(text=OLD_PRICE_MARKUP % '\nRs. 45,000\n')
        self.assertEqual(extract_price(sel.css('.old-price::text')),
                         'Rs. 45,000')

    def test_multiple_text_nodes_like_real_page(self):
        # Mirrors the original page shape: whitespace node + a node whose
        # second line holds the price (the old code did getall()[1]
        # .split('\n')[1], which crashed whenever this shape changed).
        markup = OLD_PRICE_MARKUP % (
            '\n'
            '            \n'
            'Web Price\nRs. 45,000\n'
        )
        sel = Selector(text=markup)
        self.assertEqual(extract_price(sel.css('.old-price::text')),
                         'Rs. 45,000')

    def test_plain_price_text(self):
        sel = Selector(text='<span id="item_price">29,990</span>')
        self.assertEqual(extract_price(sel.css('#item_price::text')),
                         '29,990')

    def test_label_only_returns_none_not_raw_blob(self):
        sel = Selector(text=OLD_PRICE_MARKUP % '\nOld Price\n')
        self.assertIsNone(extract_price(sel.css('.old-price::text')))

    def test_missing_element_returns_none(self):
        sel = Selector(text='<div></div>')
        self.assertIsNone(extract_price(sel.css('.old-price::text')))

    def test_none_selector_returns_none(self):
        self.assertIsNone(extract_price(None))


class ParseMaxPageTests(unittest.TestCase):
    def test_plain_integer(self):
        self.assertEqual(parse_max_page('4'), 4)

    def test_surrounding_whitespace(self):
        self.assertEqual(parse_max_page('  12 \n'), 12)

    def test_none_does_not_raise(self):
        # Regression: int(None) used to abort every subsequent page.
        self.assertIsNone(parse_max_page(None))

    def test_empty_and_whitespace(self):
        self.assertIsNone(parse_max_page(''))
        self.assertIsNone(parse_max_page('   '))

    def test_non_numeric_text(self):
        self.assertIsNone(parse_max_page('Next'))
        self.assertIsNone(parse_max_page('>'))

    def test_non_positive(self):
        self.assertIsNone(parse_max_page('0'))


class PaginationDecisionTests(unittest.TestCase):
    def test_parsed_cap_preserves_original_rule(self):
        self.assertTrue(
            should_continue_pagination(2, 4, ['/a', '/b']))
        self.assertTrue(
            should_continue_pagination(4, 4, ['/a']))
        self.assertFalse(
            should_continue_pagination(5, 4, ['/a']))

    def test_missing_cap_uses_fallback(self):
        self.assertTrue(
            should_continue_pagination(2, None, ['/a']))
        self.assertTrue(
            should_continue_pagination(FALLBACK_MAX_PAGE, None, ['/a']))
        self.assertFalse(
            should_continue_pagination(FALLBACK_MAX_PAGE + 1, None,
                                       ['/a']))

    def test_empty_detail_links_always_stops(self):
        self.assertFalse(
            should_continue_pagination(1, 10, []))
        self.assertFalse(
            should_continue_pagination(1, None, []))
        self.assertFalse(
            should_continue_pagination(FALLBACK_MAX_PAGE, 100, []))


@unittest.skipIf(TextResponse is None, 'scrapy not installed')
class SpiderFlowTests(unittest.TestCase):
    def setUp(self):
        # page_number is a class attribute; reset for every test.
        MobileSpider.page_number = 2
        LaptopSpider.page_number = 2

    def test_missing_pager_keeps_crawling_instead_of_crashing(self):
        spider = MobileSpider()
        response = make_response(
            'https://buyabans.com/mobile-phones?page=1', LIST_HTML)
        results = list(spider.parse(response))

        # Two detail requests plus one next-page request (fallback mode),
        # no exception raised despite the absent pagination element.
        detail_requests = [r for r in results
                           if getattr(r, 'callback', None)
                           == spider._parse_detail]
        page_requests = [r for r in results if r not in detail_requests]
        self.assertEqual(len(detail_requests), 2)
        self.assertEqual(len(page_requests), 1)
        self.assertEqual(spider.page_number, 3)
        self.assertIn('page=2', page_requests[0].url)

    def test_parsed_pager_resolves_cap_normally(self):
        spider = MobileSpider()
        response = make_response(
            'https://buyabans.com/mobile-phones?page=1',
            LIST_HTML_WITH_PAGER)
        results = list(spider.parse(response))
        page_requests = [r for r in results
                         if getattr(r, 'callback', None) !=
                         spider._parse_detail]
        self.assertEqual(len(page_requests), 1)
        self.assertEqual(spider.page_number, 3)

    def test_empty_listing_ends_crawl(self):
        spider = MobileSpider()
        response = make_response(
            'https://buyabans.com/mobile-phones?page=99',
            EMPTY_LIST_HTML)
        self.assertEqual(list(spider.parse(response)), [])
        self.assertEqual(spider.page_number, 2)

    def test_detail_page_missing_fields_keeps_partial_item(self):
        spider = MobileSpider()
        # Only the URL exists: no name, price, old price, image, etc.
        response = make_response(
            'https://buyabans.com/mobile-phones/bare',
            '<html><body><div id="product"></div></body></html>')
        items = list(spider._parse_detail(response))
        self.assertEqual(len(items), 1)
        item = items[0]
        for field in ('product_name', 'product_price', 'product_model',
                      'product_data', 'product_old_price',
                      'product_image'):
            self.assertIsNone(item[field], msg=field)
        self.assertEqual(item['detailed_url'],
                         'https://buyabans.com/mobile-phones/bare')

    def test_detail_page_complete_multiline_prices(self):
        spider = MobileSpider()
        html = FULL_DETAIL_HTML.format(
            old_price=OLD_PRICE_MARKUP % '\nWeb Price\nRs. 45,000\n')
        response = make_response(
            'https://buyabans.com/mobile-phones/test-phone', html)
        item = next(spider._parse_detail(response))
        self.assertEqual(item['product_name'], 'Test Phone')
        self.assertEqual(item['product_price'], 'Rs. 29,990')
        self.assertEqual(item['product_model'], 'Model No: T-1')
        self.assertEqual(item['product_data'],
                         'A short product description.')
        self.assertEqual(item['product_old_price'], 'Rs. 45,000')
        self.assertEqual(item['product_image'],
                         'https://cdn.example.com/phone.jpg')
        self.assertEqual(item['detailed_url'],
                         'https://buyabans.com/mobile-phones/test-phone')

    def test_detail_page_single_old_price_node_no_index_error(self):
        # Regression for the old .getall()[1] IndexError when the
        # old-price node exists but yields a single text fragment.
        spider = LaptopSpider()
        html = FULL_DETAIL_HTML.format(
            old_price=OLD_PRICE_MARKUP % 'Rs. 45,000')
        # Laptop uses strong::text for product_data; emulate it.
        html = html.replace(
            '<p class="intro">A short product description.</p>',
            '<strong>Specs text</strong>')
        response = make_response(
            'https://buyabans.com/computers/test-laptop', html)
        item = next(spider._parse_detail(response))
        self.assertEqual(item['product_old_price'], 'Rs. 45,000')
        self.assertEqual(item['product_data'], 'Specs text')

    def test_laptop_missing_pager_uses_fallback_path(self):
        spider = LaptopSpider()
        response = make_response(
            'https://buyabans.com/computers?page=1', LIST_HTML)
        results = list(spider.parse(response))
        page_requests = [r for r in results
                         if getattr(r, 'callback', None) !=
                         spider._parse_detail]
        self.assertEqual(len(page_requests), 1)
        self.assertIn('computers?page=2', page_requests[0].url)


if __name__ == '__main__':
    unittest.main(verbosity=2)
