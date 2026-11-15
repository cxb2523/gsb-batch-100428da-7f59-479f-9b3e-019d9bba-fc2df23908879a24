#! /usr/bin/env python3

import json
import logging
import time

import geocoder
import lxml.html
import requests


WITHIN_1_KM_CONFIDENCE_SCORE = 8
FORMATTING_SPACE = '  '
GEOCODING_ATTEMPTS = 5
GEOCODING_WAIT = 2

BASE_URL = 'http://tylercowensethnicdiningguide.com/'
LOCATIONS_FILE = 'locations.json'

# Per-restaurant failure reasons, recorded in the run summary
FETCH_FAILED = 'fetch_failed'
ADDRESS_NOT_FOUND = 'address_not_found'
GEOCODING_FAILED = 'geocoding_failed'
LOW_CONFIDENCE = 'low_confidence'

logger = logging.getLogger(__file__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


class MissingApiKeyError(RuntimeError):
    """Geocoding returned REQUEST_DENIED: a configuration error that aborts the whole run."""


class RestaurantError(Exception):
    """A single restaurant could not be processed; the run continues with the others."""

    def __init__(self, reason, detail=''):
        self.reason = reason
        self.detail = detail
        super().__init__(reason if not detail else '{}: {}'.format(reason, detail))


def fetch_page(fetch, url):
    return fetch(url)


def find_review_links(doc):
    return doc.xpath('//h3[text()="Current Favorites"]/following-sibling::ul/li/div/a')


def extract_address(doc):
    # Only one restaurant has multiple addresses, so just keep the first
    addresses = doc.xpath('//a[contains(@href, "google.com/maps") and not(text()="Google")]/text()')
    return addresses[0] if addresses else None


def geocode_with_retries(geocode, query, attempts=GEOCODING_ATTEMPTS, wait=GEOCODING_WAIT, sleep=time.sleep):
    """Geocode `query`, retrying on transient failures with an exponential backoff.

    Returns the geocoding result, or None if all attempts failed.
    Raises MissingApiKeyError on REQUEST_DENIED (missing/invalid API key).
    """
    for attempt in range(attempts):
        sleep(wait ** attempt)
        geocoded = geocode(query)
        if geocoded:
            return geocoded
        if geocoded is not None and getattr(geocoded, 'status', None) == 'REQUEST_DENIED':
            raise MissingApiKeyError(
                "`GOOGLE_API_KEY` must be set in the environment; find yours at "
                "`https://console.cloud.google.com/apis/credentials`")
        logger.warning("Failed geocoding {}, retrying now".format(query))
    return None


def process_restaurant(name, url, fetch, geocode, sleep=time.sleep,
                       attempts=GEOCODING_ATTEMPTS, wait=GEOCODING_WAIT):
    """Fetch and geocode one restaurant. Returns a location dict.

    Raises RestaurantError (with a machine-readable `reason`) on any
    per-restaurant failure, and MissingApiKeyError on REQUEST_DENIED.
    """
    try:
        page = fetch_page(fetch, url)
    except Exception as error:
        raise RestaurantError(FETCH_FAILED, str(error)) from error

    doc = lxml.html.fromstring(page.text)
    address = extract_address(doc)
    if not address:
        if not doc.xpath('//a'):
            logger.debug("The webpage at {} was probably malformed".format(url))
        raise RestaurantError(ADDRESS_NOT_FOUND)

    geocoded = geocode_with_retries(geocode, address, attempts=attempts, wait=wait, sleep=sleep)
    if geocoded is None:
        raise RestaurantError(GEOCODING_FAILED,
                              'no result after {} attempts'.format(attempts))

    if 'coordinates' not in geocoded.geometry:
        # Try again, using the name of the restaurant and the city-state
        city = address.split(',')[-2].strip()
        state = address.split(',')[-1].strip()
        alternative_search = '{}, {}, {}'.format(name, city, state)
        geocoded = geocode_with_retries(geocode, alternative_search,
                                        attempts=attempts, wait=wait, sleep=sleep)
        if geocoded is None or 'coordinates' not in geocoded.geometry:
            raise RestaurantError(GEOCODING_FAILED, 'no coordinates in geocoding result')
    coordinates = geocoded.geometry['coordinates']

    if geocoded.confidence < WITHIN_1_KM_CONFIDENCE_SCORE:
        raise RestaurantError(LOW_CONFIDENCE,
                              'confidence {} < {}'.format(geocoded.confidence,
                                                          WITHIN_1_KM_CONFIDENCE_SCORE))

    logger.info(name)
    logger.info(FORMATTING_SPACE + url)
    logger.info(FORMATTING_SPACE + address)
    logger.info(FORMATTING_SPACE + str(coordinates))
    return {'name': name, 'url': url, 'address': address, 'coordinates': coordinates}


def collect_locations(fetch, geocode, sleep=time.sleep,
                      attempts=GEOCODING_ATTEMPTS, wait=GEOCODING_WAIT):
    """Scrape every restaurant in the Current Favorites list.

    Returns (locations, failures). A failing restaurant never aborts the run:
    it is recorded in `failures` as {'name', 'url', 'reason'} and processing
    continues. MissingApiKeyError (REQUEST_DENIED) still aborts the whole run.
    """
    page = fetch_page(fetch, BASE_URL)
    doc = lxml.html.fromstring(page.text)

    review_links = find_review_links(doc)
    logger.info('Found {} restaurants listed on TCEDG.com'.format(len(review_links)))

    locations = []
    failures = []
    for review in review_links:
        (url, ) = review.xpath('@href')
        (name, ) = review.xpath('text()')
        name = name.strip().encode('ascii', 'ignore').decode('utf-8')

        try:
            location = process_restaurant(name, url, fetch, geocode,
                                          sleep=sleep, attempts=attempts, wait=wait)
        except MissingApiKeyError:
            raise
        except RestaurantError as error:
            logger.warning("Skipping {} ({}): {}".format(name, url, error))
            failures.append({'name': name, 'url': url, 'reason': error.reason})
        else:
            locations.append(location)
    return locations, failures


def write_locations(locations, path=LOCATIONS_FILE):
    # Sort the output by name, to make git diffs of `locations.json` more readable
    locations = sorted(locations, key=lambda x: x['name'])
    with open(path, 'w') as file_:
        json.dump(locations, file_, ensure_ascii=False, indent=4)


def log_summary(locations, failures):
    logger.info('Scraping finished: {} restaurant(s) written, {} failed'
                .format(len(locations), len(failures)))
    for failure in failures:
        logger.warning(FORMATTING_SPACE + '{} ({}) — {}'.format(
            failure['name'], failure['url'], failure['reason']))


def main():
    locations, failures = collect_locations(requests.get, geocoder.google)
    # Always write the file, even if every restaurant failed: the map page
    # needs a valid (possibly empty) JSON array.
    write_locations(locations)
    log_summary(locations, failures)
    logger.info("Successfully finished scraping, and updated the JSON file")


if __name__ == '__main__':
    main()
