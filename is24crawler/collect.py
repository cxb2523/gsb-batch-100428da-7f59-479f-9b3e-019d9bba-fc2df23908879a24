import datetime

from is24crawler import settings
from is24crawler.csvstore import CsvStore, build_apartment
from is24crawler.geocoding import GeocodeError, GoogleMapsGeocodingClient, geocode_address
from is24crawler.logging_utils import safe_print
from is24crawler.pending import PendingStore, new_pending_record

INVALID_CRITERIA_MARKERS = ("nach Vereinbarung", "-")


def parse_criteria(criteria_set):
    """
    Parses price, size and room count from one apartment's three criteria strings.
    :raises ValueError: if the criteria set cannot be turned into a fixed price listing.
    """
    if any(marker in value for value in criteria_set for marker in INVALID_CRITERIA_MARKERS):
        raise ValueError("price range or missing value in criteria set: {}".format(criteria_set))

    try:
        price = float(criteria_set[0].split()[0].replace(".", "").replace(",", "."))
        size = float(criteria_set[1].split()[0].replace(",", "."))
        rooms = float(criteria_set[2].replace(",", "."))
    except (IndexError, ValueError) as error:
        raise ValueError("unparsable criteria set {}: {}".format(criteria_set, error))
    return price, size, rooms


def split_criteria(criteria):
    """Splits the flat list of criteria values into one list per apartment."""
    return [criteria[settings.NUM_CRITERIA * index:settings.NUM_CRITERIA * index + settings.NUM_CRITERIA:1]
            for index in range(len(criteria) // settings.NUM_CRITERIA)]


def collect_page(criteria, addresses, date, geocoder, timestamp = None):
    """
    Turns the addresses and criteria of one result page into apartments and failures.

    An apartment is returned as apartment item if its address could be geocoded and as
    pending record if geocoding failed. A failing geocoding request never aborts the page.
    :return: a tuple (apartments, failures).
    """
    apartments = []
    failures = []
    criteria_sets = split_criteria(criteria)
    for index, address in enumerate(addresses):
        try:
            price, size, rooms = parse_criteria(criteria_sets[index])
        except (IndexError, ValueError) as error:
            safe_print("skipping invalid criteria set: {}".format(error))
            continue

        try:
            lat, lng = geocode_address(address, geocoder)
        except GeocodeError as error:
            safe_print("queuing apartment '{}' for later retry ({}): {}".format(address, error.reason, error.detail))
            failures.append(new_pending_record(price, size, rooms, address, date, error, timestamp))
            continue

        apartments.append(build_apartment(price, size, rooms, address, lat, lng, date))
    return apartments, failures


def retry_pending(geocoder = None, csv_path = None, pending_path = None, timestamp = None):
    """
    Retries geocoding for all apartments stored in the pending file.

    Apartments that can be geocoded now are appended to the main CSV and removed from the
    pending file; apartments that still fail stay in the file with an updated reason.
    Running this repeatedly is safe: already written rows are not appended twice.
    :return: a dict with the counters ``retried``, ``recovered``, ``skipped`` and ``failed``.
    """
    geocoder = geocoder or GoogleMapsGeocodingClient()
    pending = PendingStore(pending_path).load()
    records = pending.records
    summary = {"retried": len(records), "recovered": 0, "skipped": 0, "failed": 0}
    if not records:
        return summary

    csv_store = CsvStore(csv_path).load()
    try:
        for record in records:
            if csv_store.contains(record):
                # already present from an earlier attempt: nothing to add, just stop tracking it
                pending.remove(record)
                summary["skipped"] += 1
                continue

            try:
                lat, lng = geocode_address(record["address"], geocoder)
            except GeocodeError as error:
                pending.mark_failed(record, error, timestamp)
                summary["failed"] += 1
                safe_print("retry failed for '{}' ({}): {}".format(record["address"], error.reason, error.detail))
                continue

            csv_store.append(build_apartment(record["price"], record["size"], record["rooms"], record["address"],
                                             lat, lng, record["date"]))
            pending.remove(record)
            summary["recovered"] += 1
    finally:
        csv_store.close()
        pending.save()
    return summary


def today():
    return datetime.datetime.now().strftime("%Y-%m-%d")
