import os

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from is24crawler import settings
from is24crawler.collect import collect_page, retry_pending, today
from is24crawler.geocoding import GoogleMapsGeocodingClient
from is24crawler.resultpage import ResultPage


class Immoscout24Bot(scrapy.Spider):
    name = "immoscout24bot"

    def __init__(self, geocoder = None, *args, **kwargs):
        super(Immoscout24Bot, self).__init__(*args, **kwargs)
        # injectable so tests can run against faked geocoding responses
        self.geocoder = geocoder or GoogleMapsGeocodingClient()

    def start_requests(self):
        for i in range(settings.PAGE_START, settings.PAGE_END + 1):
            url = 'https://www.immobilienscout24.de/Suche/S-T/P-{0}/Wohnung-Miete/Berlin/Berlin'.format(i)
            yield scrapy.Request(url = url, callback = self.parse)

    def parse(self, response):
        criteria = list(
            filter(lambda x: len(x) > 0, (map(str.strip, response.css(settings.CRITERIA_SELECTOR).extract()))))
        if len(criteria) % settings.NUM_CRITERIA != 0:
            raise AssertionError(
                "Number of extracted criteria is not divisible by {}: Extracted criteria are not uniquely assignable "
                "to an apartment.".format(settings.NUM_CRITERIA))

        addresses = list(map(str.strip, response.css(settings.ADDRESS_SELECTOR).extract()))
        if len(addresses) != len(criteria) / settings.NUM_CRITERIA:
            raise AssertionError(
                "Number of extracted criteria sets ({}) does not match number of extracted addresses ({})".format(
                    len(criteria), len(addresses)))

        apartments, failures = collect_page(criteria, addresses, today(), self.geocoder)
        page = response.url.split("/")[5][2:]
        yield ResultPage(apartments = apartments, failures = failures, page = page)


def run(retry_failed = True):
    """
    Runs the crawler and afterwards retries the apartments whose geocoding failed.
    Failures are tracked in settings.PENDING_CSV_FILE_PATH and can therefore also be
    retried later by calling ``is24crawler.collect.retry_pending()``.
    """
    os.environ["SCRAPY_SETTINGS_MODULE"] = "is24crawler.settings"
    print("------SETTINGS------ csv path: {}".format(settings.CSV_FILE_PATH))
    print("------SETTINGS------ pending file: {}".format(settings.PENDING_CSV_FILE_PATH))
    print("------SETTINGS------ start page: {}".format(settings.PAGE_START))
    print("------SETTINGS------ end page: {}".format(settings.PAGE_END))

    process = CrawlerProcess(get_project_settings())
    process.crawl(Immoscout24Bot)
    process.start()

    if retry_failed:
        summary = retry_pending()
        print("------RETRY------ retried: {retried}, recovered: {recovered}, still failing: {failed}".format(
            **summary))


if __name__ == "__main__":
    run()
