import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.utils.log import configure_logging
from scrapy.utils.project import get_project_settings
from scrapy import signals
import os
import shutil
import sys

import crawl_summary

curfilePath = os.path.abspath(__file__)
curDir = os.path.abspath(os.path.join(curfilePath, os.pardir))
tmpDir = os.path.abspath(os.path.join(curDir,'demo/tmp/'))
resultFile = os.path.abspath(os.path.join(curDir,'demo/results.csv'))
statusFile = os.path.abspath(os.path.join(curDir,'demo/search_status.json'))

# Spider names launched for every search (crawl order)
SITES = crawl_summary.SITES

print tmpDir
print resultFile
# remove old crawling data
try:
    shutil.rmtree(tmpDir)
except:
    pass

# get the product name from the arguments
pruduct = sys.argv[1]

configure_logging({'LOG_FORMAT': '%(levelname)s: %(message)s'})
s=get_project_settings()

# Change the depth limit here
# s['DEPTH_LIMIT'] = 2
process = CrawlerProcess(s)

# Per-site crawl observations. They let the manifest tell "the site was
# scraped but had nothing" apart from "the site could not be scraped",
# since both would otherwise leave an empty CSV behind.
crawl_stats = dict((site, {'items': 0, 'closed': False, 'errors': []})
                   for site in SITES)

def _make_handlers(site):
    stats = crawl_stats[site]
    def on_item_scraped(item, response, spider):
        stats['items'] += 1
    def on_spider_closed(spider):
        stats['closed'] = True
    def on_spider_error(failure, response, spider):
        stats['errors'].append(str(failure.value)[:200])
    return on_item_scraped, on_spider_closed, on_spider_error

# Add spiders to crawl. A failure while setting up one spider must not
# stop the other sites from being crawled.
for site in SITES:
    try:
        crawler = process.create_crawler(site)
        on_item, on_closed, on_error = _make_handlers(site)
        crawler.signals.connect(on_item, signals.item_scraped)
        crawler.signals.connect(on_closed, signals.spider_closed)
        crawler.signals.connect(on_error, signals.spider_error)
        process.crawl(crawler, product=pruduct)
    except Exception:
        # Fall back to letting the runner resolve the spider itself
        try:
            process.crawl(site, product=pruduct)
        except Exception as exc:
            crawl_stats[site]['errors'].append('spider failed to start: %s' % exc)

# Start Crawling. Even a fatal crawler error must not abort the run:
# the merge and the manifest below still have to be produced.
try:
    process.start()
except Exception as exc:
    print 'Crawler process aborted: %s' % exc

# Merge per-site CSVs into results.csv and write the per-site manifest
# (search_status.json) for this same search run.
manifest = crawl_summary.collect(tmpDir, resultFile, statusFile, pruduct,
                                 sites=SITES, crawl_stats=crawl_stats)
for entry in manifest['sites']:
    print '%s: %s (%s items) %s' % (entry['site'], entry['status'],
                                    entry['count'], entry['reason'])

print 'Crawling Completed'
