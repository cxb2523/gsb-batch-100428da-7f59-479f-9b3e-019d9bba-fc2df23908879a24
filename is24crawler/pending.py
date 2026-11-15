import datetime
import json
import os
import tempfile

from is24crawler import settings
from is24crawler.csvstore import format_identity_value
from is24crawler.geocoding import GEOCODE_REASONS
from is24crawler.logging_utils import safe_print

# All apartment features are kept in the pending file so a later successful retry
# can append a complete row to the main CSV without crawling the page again.
RECORD_FIELDS = ("price", "size", "rooms", "address", "date")
# Identifies an apartment across crawling runs; the crawl date is not part of the key so
# re-crawling the same listing updates its entry instead of queueing a second one.
KEY_FIELDS = ("price", "size", "rooms", "address")


def pending_key(record):
    """
    Key identifying an apartment inside the pending file. Crawling the same page
    twice must not create a second pending entry for the same apartment.
    """
    return tuple(format_identity_value(field, record.get(field)) for field in KEY_FIELDS)


def new_pending_record(price, size, rooms, address, date, error, timestamp = None):
    """Builds a pending record for an apartment whose geocoding failed."""
    return {
        "price": price,
        "size": size,
        "rooms": rooms,
        "address": address,
        "date": date,
        "reason": error.reason,
        "detail": error.detail,
        "attempts": 1,
        "first_failed_at": timestamp or _now(),
        "last_failed_at": timestamp or _now(),
    }


def _now():
    return datetime.datetime.now().replace(microsecond = 0).isoformat()


class PendingStore(object):
    """Read/write access to the JSON Lines file holding unsuccessfully geocoded apartments."""

    def __init__(self, path = None):
        self.path = path or settings.PENDING_CSV_FILE_PATH
        self._records = {}
        self._loaded = False

    # -- reading -----------------------------------------------------------
    def load(self):
        """Loads the file, dropping stale duplicates (last record for a key wins)."""
        self._records = {}
        self._loaded = True
        if not os.path.exists(self.path):
            return self

        with open(self.path, "r", encoding = "utf-8") as handle:
            for line_number, line in enumerate(handle, start = 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    safe_print("ignoring unreadable pending record in '{}' line {}: {!r}".format(self.path, line_number,
                                                                                           line[:120]))
                    continue
                if not self._is_valid(record):
                    safe_print("ignoring invalid pending record in '{}' line {}".format(self.path, line_number))
                    continue
                self._records[pending_key(record)] = record
        return self

    @staticmethod
    def _is_valid(record):
        if not isinstance(record, dict) or not all(field in record for field in RECORD_FIELDS):
            return False
        reason = record.get("reason")
        if not isinstance(reason, str) or not reason:
            return False
        if reason not in GEOCODE_REASONS:
            # keep records written by other versions instead of losing the apartment
            safe_print("keeping pending record with unknown reason '{}'".format(reason))
        return True

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    @property
    def records(self):
        self._ensure_loaded()
        return list(self._records.values())

    def __len__(self):
        self._ensure_loaded()
        return len(self._records)

    # -- writing -----------------------------------------------------------
    def add(self, record):
        """Adds a failed apartment, refreshing the failure information if it is already known."""
        self._ensure_loaded()
        key = pending_key(record)
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = record
        else:
            existing["date"] = record["date"]
            existing["reason"] = record["reason"]
            existing["detail"] = record["detail"]
            existing["attempts"] = existing.get("attempts", 0) + 1
            existing["last_failed_at"] = record["last_failed_at"]
        return self

    def mark_failed(self, record, error, timestamp = None):
        """Updates a still failing record with the reason of the latest attempt."""
        record["reason"] = error.reason
        record["detail"] = error.detail
        record["attempts"] = int(record.get("attempts", 0)) + 1
        record["last_failed_at"] = timestamp or _now()
        self._records[pending_key(record)] = record
        return self

    def remove(self, record):
        self._ensure_loaded()
        self._records.pop(pending_key(record), None)

    def save(self):
        """Rewrites the pending file atomically so an interrupted run cannot truncate it."""
        self._ensure_loaded()
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)

        if not self._records:
            if os.path.exists(self.path):
                os.remove(self.path)
            return

        descriptor, temporary_path = tempfile.mkstemp(prefix = ".pending-", suffix = ".tmp", dir = directory)
        try:
            with os.fdopen(descriptor, "w", encoding = "utf-8", newline = "\n") as handle:
                for record in self._records.values():
                    handle.write(json.dumps(record, ensure_ascii = False, sort_keys = True) + "\n")
            os.replace(temporary_path, self.path)
        except BaseException:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
            raise
        return self
