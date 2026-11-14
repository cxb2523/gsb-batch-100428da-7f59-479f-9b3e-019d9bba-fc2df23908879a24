#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verification test for the batch scan resilience refactor in run_scanner.py.

Uses fake crawler / analyzer / DB (no network, no Gemini, no Playwright) to check:
  1. Sites that fail to crawl are skipped for analysis but recorded in `errors`,
     and analysis results map back to the CORRECT site (no index drift).
  2. After every batch, checkpoint_progress.json holds current_index (next batch),
     analyzed results and failed sites.
  3. After a simulated interruption, resuming continues from current_index and
     does NOT re-crawl or re-insert already-completed batches.

Run:  python test_scanner_resume.py
"""

import asyncio
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows consoles default to GBK; the scanner prints emoji
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# The real `supabase` package is unusable in this environment; the scanner DB
# is faked below anyway, so stub the import to let run_scanner load.
try:
    from supabase import create_client, Client  # noqa: F401
except Exception:
    supabase_stub = types.ModuleType('supabase')
    supabase_stub.create_client = lambda *a, **k: None
    supabase_stub.Client = object
    lib_stub = types.ModuleType('supabase.lib')
    options_stub = types.ModuleType('supabase.lib.client_options')
    options_stub.ClientOptions = object
    lib_stub.client_options = options_stub
    supabase_stub.lib = lib_stub
    sys.modules['supabase'] = supabase_stub
    sys.modules['supabase.lib'] = lib_stub
    sys.modules['supabase.lib.client_options'] = options_stub

import run_scanner
from run_scanner import Scanner


# ---------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------

class FakeCrawler:
    """Crawls successfully except for domains listed in `fail_domains`."""

    def __init__(self, fail_domains=()):
        self.fail_domains = set(fail_domains)
        self.crawled_urls = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def analyze(self, url):
        self.crawled_urls.append(url)
        domain = url.replace('https://', '').split('/')[0]
        if domain in self.fail_domains:
            raise ConnectionError(f"boom: cannot reach {domain}")
        return {
            'url': url,
            'domain': domain,
            'title': f'Title of {domain}',
            'page_text': 'fake page text',
            'network': {'providers_found': [], 'detected_models': []},
        }


class FakeAnalyzer:
    """Echoes one analysis per input; can be armed to raise on call N."""

    def __init__(self, batch_size=3, explode_on_call=None):
        self.batch_size = batch_size
        self.calls = 0
        self.explode_on_call = explode_on_call
        self.seen_urls = []

    async def analyze_batch(self, crawl_data_list):
        self.calls += 1
        if self.explode_on_call is not None and self.calls == self.explode_on_call:
            raise KeyboardInterrupt("simulated interruption")
        results = []
        for data in crawl_data_list:
            self.seen_urls.append(data['url'])
            results.append({
                'url': data['url'],
                'domain': data['domain'],
                'title': data['title'],
                'category': 'Other',   # forces category_hint fallback
                'status': 'verified',
                'trust_score': 80,
            })
        return results


class FakeDB:
    def __init__(self):
        self.inserted = []

    def batch_insert(self, records):
        self.inserted.extend(records)
        return len(records)

    def get_all(self, limit=10000):
        return []


def make_sites(n):
    return [
        {
            'url': f'https://site{i}.example.com/',
            'domain': f'site{i}.example.com',
            'title': f'Site {i}',
            'discovered_via': f'query-{i}',
            'category_hint': f'Category-{i}',
        }
        for i in range(n)
    ]


def run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------
# Test 1: partial crawl failure inside a batch
# ---------------------------------------------------------------

def test_partial_failure(tmpdir):
    run_scanner.PROGRESS_FILE = os.path.join(tmpdir, 'checkpoint_progress.json')
    run_scanner.CHECKPOINT_FILE = os.path.join(tmpdir, 'checkpoint_sites.json')

    sites = make_sites(7)          # batches: [0,1,2] [3,4,5] [6]
    fail = {'site1.example.com', 'site4.example.com'}

    crawler = FakeCrawler(fail_domains=fail)
    analyzer = FakeAnalyzer(batch_size=3)
    db = FakeDB()

    scanner = Scanner(batch_size=3)
    scanner.db = db
    run_scanner.ImperialCrawler = lambda: crawler
    run_scanner.GeminiAnalyzer = lambda batch_size: analyzer

    result = run_async(scanner.process(sites))

    # --- successful sites analyzed & saved, failed ones recorded
    assert scanner.stats['crawled'] == 5, scanner.stats
    assert scanner.stats['analyzed'] == 5, scanner.stats
    assert scanner.stats['saved'] == 5, scanner.stats
    assert scanner.stats['errors'] == 2, scanner.stats
    assert len(db.inserted) == 5
    assert len(result) == 5, "process must return all analyzed results"

    # --- no misalignment: each analysis carries ITS OWN site's metadata
    by_url = {r['url']: r for r in db.inserted}
    for i, site in enumerate(sites):
        if site['domain'] in fail:
            assert site['url'] not in by_url, f"failed site {site['url']} was inserted!"
            continue
        rec = by_url[site['url']]
        assert rec['discovered_via'] == f'query-{i}', \
            f"discovered_via drifted for {site['url']}: {rec['discovered_via']}"
        assert rec['category'] == f'Category-{i}', \
            f"category_hint drifted for {site['url']}: {rec['category']}"

    # --- analyzer only ever saw successfully crawled urls
    assert set(analyzer.seen_urls) == set(by_url.keys())

    # --- progress checkpoint written after the final batch
    with open(run_scanner.PROGRESS_FILE, encoding='utf-8') as f:
        progress = json.load(f)
    assert progress['current_index'] == 7, progress['current_index']
    assert progress['total_sites'] == 7
    assert progress['analyzed_count'] == 5
    assert progress['error_count'] == 2
    err_domains = {e['domain'] for e in progress['errors']}
    assert err_domains == fail, err_domains
    assert all(e['phase'] == 'crawl' and e['error'] for e in progress['errors'])
    assert {e['batch'] for e in progress['errors']} == {1, 2}, "errors must be batch-identifiable"

    print("✅ Test 1 passed: partial failure isolated, results aligned, errors recorded")
    return True


# ---------------------------------------------------------------
# Test 2: interruption mid-run, then resume without duplicates
# ---------------------------------------------------------------

def test_interrupt_and_resume(tmpdir):
    run_scanner.PROGRESS_FILE = os.path.join(tmpdir, 'checkpoint_progress.json')
    run_scanner.CHECKPOINT_FILE = os.path.join(tmpdir, 'checkpoint_sites.json')

    sites = make_sites(7)
    fail = {'site1.example.com', 'site4.example.com'}

    # discovery checkpoint so run(from_checkpoint=True) finds the site list
    run_scanner.save_checkpoint(sites, phase="discovery_complete")

    # ---- Run 1: analyzer explodes on the 2nd batch call (simulated Ctrl-C)
    crawler1 = FakeCrawler(fail_domains=fail)
    analyzer1 = FakeAnalyzer(batch_size=3, explode_on_call=2)
    db1 = FakeDB()

    scanner1 = Scanner(batch_size=3)
    scanner1.db = db1
    run_scanner.ImperialCrawler = lambda: crawler1
    run_scanner.GeminiAnalyzer = lambda batch_size: analyzer1

    interrupted = False
    try:
        run_async(scanner1.run(from_checkpoint=True))
    except KeyboardInterrupt:
        interrupted = True
    assert interrupted, "run should have been interrupted on batch 2"

    # batch 1 fully done: 3 crawled (site1 failed) -> 2 analyzed/saved.
    # batch 2 was crawled but its analysis exploded before the checkpoint
    # write, so only batch 1's results are persisted.
    assert len(db1.inserted) == 2, f"batch 1 should have saved 2, got {len(db1.inserted)}"
    assert crawler1.crawled_urls == [s['url'] for s in sites[:6]]

    with open(run_scanner.PROGRESS_FILE, encoding='utf-8') as f:
        progress = json.load(f)
    assert progress['current_index'] == 3, \
        f"current_index must point at the NEXT batch (3), got {progress['current_index']}"
    assert progress['analyzed_count'] == 2
    assert progress['error_count'] == 1

    # ---- Run 2: resume - must NOT redo batch 1
    crawler2 = FakeCrawler(fail_domains=fail)
    analyzer2 = FakeAnalyzer(batch_size=3)   # no explosion this time
    db2 = FakeDB()
    db2.inserted = list(db1.inserted)        # same "database" as run 1

    scanner2 = Scanner(batch_size=3)
    scanner2.db = db2
    run_scanner.ImperialCrawler = lambda: crawler2
    run_scanner.GeminiAnalyzer = lambda batch_size: analyzer2

    run_async(scanner2.run(from_checkpoint=True))

    # resume must only crawl sites from index 3 onward
    expected_resume_urls = [s['url'] for s in sites[3:]]
    assert crawler2.crawled_urls == expected_resume_urls, \
        f"resume re-crawled finished batches: {crawler2.crawled_urls}"

    # no duplicate inserts: 2 (run 1) + 3 new (sites 3,5,6; site4 fails) = 5 unique
    all_urls = [r['url'] for r in db2.inserted]
    assert len(all_urls) == len(set(all_urls)) == 5, \
        f"duplicate or missing inserts: {all_urls}"
    assert scanner2.stats['saved'] == 5, scanner2.stats
    assert scanner2.stats['errors'] == 2, scanner2.stats

    # resumed records still map to the right site metadata
    by_url = {r['url']: r for r in db2.inserted}
    for i in (3, 5, 6):
        assert by_url[sites[i]['url']]['discovered_via'] == f'query-{i}'
        assert by_url[sites[i]['url']]['category'] == f'Category-{i}'

    # final checkpoint: everything done
    with open(run_scanner.PROGRESS_FILE, encoding='utf-8') as f:
        progress = json.load(f)
    assert progress['current_index'] == 7
    assert progress['analyzed_count'] == 5
    assert progress['error_count'] == 2

    # ---- Run 3: everything already done -> no crawling at all
    crawler3 = FakeCrawler()
    run_scanner.ImperialCrawler = lambda: crawler3
    scanner3 = Scanner(batch_size=3)
    scanner3.db = FakeDB()
    run_async(scanner3.run(from_checkpoint=True))
    assert crawler3.crawled_urls == [], "fully completed run must not reprocess"

    print("✅ Test 2 passed: resume from current_index, no duplicate processing")
    return True


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, 't1'), exist_ok=True)
        test_partial_failure(os.path.join(tmp, 't1'))
        os.makedirs(os.path.join(tmp, 't2'), exist_ok=True)
        test_interrupt_and_resume(os.path.join(tmp, 't2'))
    print("\n🎉 All tests passed")
