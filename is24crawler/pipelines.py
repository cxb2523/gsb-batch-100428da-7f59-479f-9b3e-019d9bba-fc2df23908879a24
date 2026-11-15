from is24crawler import settings
from is24crawler.csvstore import CsvStore
from is24crawler.pending import PendingStore


class CsvWriterPipeline(object):
    """
    Appends successfully geocoded apartments to the main CSV and failed ones to the
    pending file. Both files are rewritten/appended atomically per page, so a page
    can be processed again without duplicating rows.
    """

    def __init__(self, csv_path = None, pending_path = None):
        self.csv_path = csv_path or settings.CSV_FILE_PATH
        self.pending_path = pending_path or settings.PENDING_CSV_FILE_PATH
        self.csv_store = None
        self.pending_store = None

    def open_spider(self, spider):
        self.csv_store = CsvStore(self.csv_path).open()
        self.pending_store = PendingStore(self.pending_path).load()

    def close_spider(self, spider):
        self.pending_store.save()
        self.csv_store.close()

    def process_item(self, item, spider):
        """
        writes a csv file for each crawled page.
        format: price in eur|size in m^2|number of rooms|address|latitude|longitude|UTM zone|UTM latitude band|
                UTM easting coord|UTM northing coord|date
        """
        written = 0
        for apartment in item["apartments"]:
            if self.csv_store.append(apartment):
                written += 1
            # a previously queued failure that now geocoded successfully is no longer pending
            self.pending_store.remove(apartment)

        failures = item.get("failures") or []
        queued = 0
        for failure in failures:
            # do not queue an apartment that already made it into the main CSV in an earlier run
            if self.csv_store.contains(failure):
                continue
            self.pending_store.add(failure)
            queued += 1
        self.pending_store.save()

        print("page {} processed: {} row(s) written, {} apartment(s) queued for retry.".format(
            item["page"], written, queued))
        return item
