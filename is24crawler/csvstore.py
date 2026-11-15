import csv
import os

import utm

from is24crawler import settings
from is24crawler.apartment import Apartment

CSV_DELIMITER = "|"
CSV_FIELDS = ("price", "size", "rooms", "address", "lat", "lng", "zone", "band", "east", "north", "date")
# Fields identifying an apartment independently of its coordinates and crawl date. Used to
# keep the main CSV free of duplicate rows when pages or pending retries are processed again.
IDENTITY_FIELDS = ("price", "size", "rooms", "address")


def format_identity_value(field, value):
    """Normalises a feature value so identities read from the CSV match freshly built ones."""
    if field in ("price", "size", "rooms"):
        try:
            return str(float(value))
        except (TypeError, ValueError):
            return "" if value is None else str(value).strip()
    return "" if value is None else str(value).strip()


def row_identity(row):
    """Builds the duplicate detection key from a mapping of CSV fields."""
    return tuple(format_identity_value(field, row.get(field)) for field in IDENTITY_FIELDS)


class CsvStore(object):
    """Appends apartment rows to the main CSV without writing the same apartment twice."""

    def __init__(self, path = None):
        self.path = path or settings.CSV_FILE_PATH
        self._file = None
        self._writer = None
        self._identities = set()
        self._loaded = False

    def load(self):
        """Reads the identities of all rows already present in the main CSV."""
        self._identities = set()
        self._loaded = True
        if not os.path.exists(self.path):
            return self

        with open(self.path, "r", encoding = "utf-8", errors = "replace", newline = "") as handle:
            for row in csv.reader(handle, delimiter = CSV_DELIMITER):
                if len(row) != len(CSV_FIELDS):
                    continue
                self._identities.add(row_identity(dict(zip(CSV_FIELDS, row))))
        return self

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    def open(self):
        self._ensure_loaded()
        if self._file is not None:
            return self
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        self._file = open(self.path, "a", encoding = "utf-8", newline = "")
        self._writer = csv.writer(self._file, delimiter = CSV_DELIMITER)
        return self

    def close(self):
        if self._file is not None:
            self._file.close()
        self._file = None
        self._writer = None

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def contains(self, apartment):
        """Checks whether an apartment is already present in the main CSV."""
        self._ensure_loaded()
        return row_identity(apartment) in self._identities

    def append(self, apartment):
        """
        Appends an apartment row.
        :return: True if the row was written, False if the apartment was already present.
        """
        self._ensure_loaded()
        identity = row_identity(apartment)
        if identity in self._identities:
            return False

        if self._writer is None:
            self.open()
        self._writer.writerow([apartment[field] for field in CSV_FIELDS])
        self._file.flush()
        self._identities.add(identity)
        return True


def build_apartment(price, size, rooms, address, lat, lng, date):
    """Builds an apartment item, including its derived UTM coordinates."""
    utm_coord = utm.from_latlon(lat, lng)
    return Apartment(price = price, size = size, rooms = rooms, address = address, lat = lat, lng = lng,
                     zone = utm_coord[2], band = utm_coord[3], east = utm_coord[0], north = utm_coord[1], date = date)
