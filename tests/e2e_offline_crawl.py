"""End-to-end offline crawl for both Abans spiders.

A local HTML fixture directory plays the role of buyabans.com and the
crawl runs through the real Scrapy engine with network access disabled
(no middleware can leave the machine).  It proves that:

* a detail page missing name/price/old-price/image still yields an item
  with empty values and a valid detailed_url, and the crawl continues;
* a listing page without the fixed-position pagination element no longer
  raises int(None) -- crawling advances until detail links run out or the
  fallback cap is reached.

Run:
    python -I tests/e2e_offline_crawl.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapy.crawler import CrawlerProcess
from scrapy.http import HtmlResponse


FIXTURE_URL_MAP = {}


class FixtureDownloaderMiddleware:
    """Serves local HTML fixtures instead of performing any download.

    Windows file systems reject '?' in names, so the listing pages
    (requested as .../listing?page=N) are stored as plain files and
    mapped here.  Every served URL must be a pre-registered file:// URL,
    so nothing can leave the machine.
    """

    def process_request(self, request, spider):
        target = FIXTURE_URL_MAP.get(request.url, request.url)
        if not target.startswith('file:///'):
            raise RuntimeError(
                'offline test refused non-file URL: ' + request.url)
        path = target[len('file:///'):]
        if not os.path.exists(path) and os.path.exists(path + '.html'):
            path = path + '.html'
        with open(path, 'rb') as fh:
            body = fh.read()
        return HtmlResponse(url=request.url, body=body,
                            encoding='utf-8', request=request)

FIXTURES = tempfile.mkdtemp(prefix='abans_fixtures_')

OLD_PRICE_BLOCK = (
    '<div id="product"><div class="primary-box row all-details">'
    '<div class="pb-right-column col-xs-12 col-md-5 col-sm-12 '
    'detail-con-padding"><div class="row"><div>'
    '<div class="old-price">\nWeb Price\nRs. 45,000\n</div>'
    '</div></div></div></div></div>'
)

DETAIL_GOOD = """
<html><body>
  <h1 class="product-name">
    Good Product
  </h1>
  <span id="item_price">
    Rs. 29,990
  </span>
  <span class="modal-no">Model No: G-1</span>
  <p class="intro">Description text</p>
  <strong>Laptop spec text</strong>
  <img id="zoom_03" src="https://cdn.example.com/good.jpg" />
  {old_price}
</body></html>
""".format(old_price=OLD_PRICE_BLOCK)

# Detail page with nearly every field absent (no old-price node at all).
DETAIL_BARE = """
<html><body>
  <h1 class="product-name">
    Partial Product
  </h1>
  <div id="product"></div>
</body></html>
"""

LISTING_PAGE = """
<html><body>
  <ul id="filter-container">
    <li><a href="{base}/good">Good</a></li>
    <li><a href="{base}/bare">Bare</a></li>
  </ul>
  <!-- deliberately NO sortPagiBar pagination element -->
</body></html>
"""

EMPTY_PAGE = '<html><body><ul id="filter-container"></ul></body></html>'


def build_fixtures(section):
    base_dir = os.path.join(FIXTURES, section)
    os.makedirs(base_dir, exist_ok=True)
    dir_url = 'file:///' + base_dir.replace(os.sep, '/')
    listing_url = dir_url + '/listing'
    with open(os.path.join(base_dir, 'good.html'), 'w',
              encoding='utf-8') as fh:
        fh.write(DETAIL_GOOD)
    with open(os.path.join(base_dir, 'bare.html'), 'w',
              encoding='utf-8') as fh:
        fh.write(DETAIL_BARE)
    # Spider requests listing?page=1 / ?page=2; stored without '?' and
    # mapped back by FixtureDownloaderMiddleware.
    page1 = os.path.join(base_dir, 'listing_page1.html')
    page2 = os.path.join(base_dir, 'listing_page2.html')
    with open(page1, 'w', encoding='utf-8') as fh:
        fh.write(LISTING_PAGE.format(base=dir_url))
    with open(page2, 'w', encoding='utf-8') as fh:
        fh.write(EMPTY_PAGE)
    FIXTURE_URL_MAP.update({
        listing_url + '?page=1':
            'file:///' + page1.replace(os.sep, '/'),
        listing_url + '?page=2':
            'file:///' + page2.replace(os.sep, '/'),
    })
    return listing_url


def patch_spider(spider_cls, base_url):
    """Point a spider at the local fixtures and block any real traffic."""
    listing = base_url + '?page=1'
    spider_cls.start_urls = [listing]
    spider_cls.base_url = base_url
    spider_cls.page_number = 2
    return spider_cls


def run():
    from abans_lk.spiders.abans_mobile_spider import (
        AbansSpider as MobileSpider)
    from abans_lk.spiders.abans_laptop_spider import (
        AbansSpider as LaptopSpider)

    mobile_base = build_fixtures('mobile-phones')
    laptop_base = build_fixtures('computers')
    patch_spider(MobileSpider, mobile_base)
    patch_spider(LaptopSpider, laptop_base)

    out_path = os.path.join(FIXTURES, 'items.jl')
    # Offline safety: fixtures are file:// URLs only; OffsiteMiddleware
    # is disabled and robots ignored so nothing can leave the machine.
    process = CrawlerProcess(settings={
        'ROBOTSTXT_OBEY': False,
        'DOWNLOADER_MIDDLEWARES': {
            '__main__.FixtureDownloaderMiddleware': 100,
            'scrapy.downloadermiddlewares.offsite.OffsiteMiddleware': None,
        },
        'FEEDS': {out_path: {'format': 'jsonlines'}},
        'LOG_LEVEL': 'ERROR',
    })
    process.crawl(MobileSpider)
    process.crawl(LaptopSpider)
    process.start()

    import json
    items = []
    with open(out_path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    print('\n=== crawled {} items ==='.format(len(items)))
    for item in items:
        print(item)

    assert len(items) == 4, 'expected 2 items per spider, got {}'.format(
        len(items))
    good = [i for i in items if i['product_name'] == 'Good Product']
    bare = [i for i in items if i['product_name'] == 'Partial Product']
    assert len(good) == 2 and len(bare) == 2

    for item in good:
        assert item['product_price'] == 'Rs. 29,990', item
        assert item['product_old_price'] == 'Rs. 45,000', item
        assert item['product_image'] == \
            'https://cdn.example.com/good.jpg', item
        assert item['detailed_url'].endswith('/good'), item

    for item in bare:
        # Missing fields must be present as null, never raise.
        for field in ('product_price', 'product_model', 'product_data',
                      'product_old_price', 'product_image'):
            assert item[field] is None, (field, item)
        assert item['detailed_url'].endswith('/bare'), item

    print('\nE2E OK: partial items retained, multiline prices parsed, '
          'missing pager did not interrupt the crawl.')


if __name__ == '__main__':
    run()
