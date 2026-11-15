# -*- coding: utf-8 -*-
"""Merge per-site crawl CSVs and record the outcome of every site.

Imported by run_crawler_demo.py after the crawl finishes, but can also be
run standalone to rebuild results.csv and the per-site manifest from
already existing per-site CSVs (useful for testing):

    python crawl_summary.py <product> [tmp_dir] [result_file] [status_file]

The manifest is a JSON document tied to one search run:

    {
      "product": "<search keyword>",
      "sites": [
        {"site": "amazon", "status": "success", "count": 12, "reason": ""},
        {"site": "ebay", "status": "zero_results", "count": 0, "reason": ""},
        {"site": "olx", "status": "failed", "count": 0,
         "reason": "no output produced (spider error, blocked or site unreachable)"},
        ...
      ]
    }

status is one of: "success" (items scraped), "zero_results" (the site was
scraped fine but had nothing for this keyword), "failed" (the site could
not be scraped; "reason" holds a short explanation).
"""
import csv
import glob
import json
import os
import sys

# Spider names launched for every search (crawl order).
SITES = ['amazon', 'ebay', 'shopclues', 'olx']

# Matches FEED_EXPORT_FIELDS in product_info_crawler/settings.py; used when
# no site produced any output so results.csv still has a valid header.
DEFAULT_HEADER = 'product_name,price,source,product_url,image_url\r\n'

STATUS_SUCCESS = 'success'
STATUS_ZERO_RESULTS = 'zero_results'
STATUS_FAILED = 'failed'


def count_data_rows(filename):
    """Number of data rows (excluding the header) in a site CSV.

    Returns None when the file is missing, empty or unreadable.
    """
    try:
        if os.path.getsize(filename) == 0:
            return None
        with open(filename, 'rb') as fin:
            rows = list(csv.reader(fin))
    except (IOError, OSError):
        return None
    if not rows:
        return None
    return max(len(rows) - 1, 0)


def merge_site_csvs(tmp_dir, result_file):
    """Merge tmp_dir/*.csv into result_file.

    Keeps the existing column order and field meanings (the header is
    copied verbatim from the first non-empty site CSV). Always writes a
    header, even when every site CSV is missing or empty, so an all-failed
    run still leaves a valid results.csv with just the header.

    Returns {site_name: data_row_count_or_None}.
    """
    counts = {}
    header_saved = False
    with open(result_file, 'wb') as fout:
        for filename in sorted(glob.glob(os.path.join(tmp_dir, '*.csv'))):
            site = os.path.splitext(os.path.basename(filename))[0]
            rows = count_data_rows(filename)
            counts[site] = rows
            if rows is None:
                continue
            with open(filename) as fin:
                header = next(fin)
                if not header_saved:
                    fout.write(header)
                    header_saved = True
                for line in fin:
                    fout.write(line)
        if not header_saved:
            fout.write(DEFAULT_HEADER)
    return counts


def classify_site(site, row_count, stats=None):
    """Build the manifest entry for one site.

    row_count: data rows in the site's CSV (None if no usable file).
    stats: optional crawl observations for this site, a dict with keys
           'items' (int), 'closed' (bool), 'errors' (list of str). When
           given, it lets us tell "site scraped, nothing found" apart from
           "site could not be scraped" even though both leave an empty CSV.
    """
    entry = {'site': site, 'status': STATUS_FAILED, 'count': 0, 'reason': ''}
    errors = []
    closed = False
    if stats:
        errors = stats.get('errors') or []
        closed = bool(stats.get('closed'))
    if row_count and row_count > 0:
        entry['status'] = STATUS_SUCCESS
        entry['count'] = row_count
    elif errors:
        entry['reason'] = errors[0]
    elif stats is not None and not closed:
        entry['reason'] = 'spider did not run to completion'
    elif row_count is not None or (stats is not None and closed):
        # Site CSV exists (header only) or the spider finished cleanly:
        # the site was reachable, it just had nothing for this keyword.
        entry['status'] = STATUS_ZERO_RESULTS
    else:
        entry['reason'] = ('no output produced '
                           '(spider error, blocked or site unreachable)')
    return entry


def write_manifest(status_file, product, entries):
    manifest = {'product': product, 'sites': entries}
    with open(status_file, 'w') as fout:
        json.dump(manifest, fout, indent=2)
    return manifest


def collect(tmp_dir, result_file, status_file, product, sites=SITES,
            crawl_stats=None):
    """Merge per-site CSVs into result_file and write the manifest for
    this search to status_file. Returns the manifest dict."""
    row_counts = merge_site_csvs(tmp_dir, result_file)
    entries = []
    for site in sites:
        stats = crawl_stats.get(site) if crawl_stats else None
        entries.append(classify_site(site, row_counts.get(site), stats))
    return write_manifest(status_file, product, entries)


if __name__ == '__main__':
    # Standalone mode: rebuild results.csv + manifest from existing tmp CSVs.
    _curDir = os.path.abspath(os.path.join(os.path.abspath(__file__), os.pardir))
    _product = sys.argv[1] if len(sys.argv) > 1 else ''
    _tmpDir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(_curDir, 'demo/tmp/')
    _resultFile = sys.argv[3] if len(sys.argv) > 3 else os.path.join(_curDir, 'demo/results.csv')
    _statusFile = sys.argv[4] if len(sys.argv) > 4 else os.path.join(_curDir, 'demo/search_status.json')
    _manifest = collect(_tmpDir, _resultFile, _statusFile, _product)
    for _entry in _manifest['sites']:
        print '%s: %s (%s) %s' % (_entry['site'], _entry['status'],
                                  _entry['count'], _entry['reason'])
