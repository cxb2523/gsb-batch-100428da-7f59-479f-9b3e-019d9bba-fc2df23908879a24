#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🏴 IMPERIAL AI ARCHIVE - Standalone Scanner
Tüm bağımlılıkları içeren tek dosya scanner
"""

import asyncio
import json
import os
from datetime import datetime
from typing import List, Dict

# Import existing modules
from discovery.ddg_scraper import DDGScraper
from discovery.search_queries import SEARCH_QUERIES
from crawler.engine import ImperialCrawler
from analyzer.gemini_analyzer import GeminiAnalyzer
from db.supabase_client import SupabaseClient

CHECKPOINT_FILE = "checkpoint_sites.json"
PROGRESS_FILE = "checkpoint_progress.json"


def save_checkpoint(sites: List[dict], phase: str = "discovery"):
    """Save checkpoint"""
    data = {
        'timestamp': datetime.now().isoformat(),
        'phase': phase,
        'total_sites': len(sites),
        'sites': sites
    }
    with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Checkpoint saved: {len(sites)} sites")


def load_checkpoint() -> List[dict]:
    """Load checkpoint"""
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"📂 Checkpoint loaded: {data.get('total_sites', 0)} sites")
        return data.get('sites', [])
    except:
        return None


def save_progress_checkpoint(analyzed: List[dict], errors: List[dict],
                             current_index: int, total: int):
    """Save progress checkpoint (analyzed results + failed sites + resume index)"""
    data = {
        'timestamp': datetime.now().isoformat(),
        'phase': 'analyzing_progress',
        'current_index': current_index,
        'total_sites': total,
        'analyzed_count': len(analyzed),
        'error_count': len(errors),
        'analyzed': analyzed,
        'errors': errors
    }
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_progress_checkpoint():
    """Load progress checkpoint"""
    if not os.path.exists(PROGRESS_FILE):
        return None
    try:
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"📂 Progress checkpoint loaded: {data.get('analyzed_count', 0)} analyzed, "
              f"{data.get('error_count', 0)} errors, resume at index {data.get('current_index', 0)}")
        return data
    except:
        return None


class ParallelDiscovery:
    """Parallel category discovery"""
    
    def __init__(self, existing_domains: set):
        self.existing_domains = existing_domains
        self.all_sites = []
        self.lock = asyncio.Lock()
    
    async def search_category(self, category: str, queries: List[str], 
                             sites_per_query: int = 10) -> List[dict]:
        """Search single category"""
        scraper = DDGScraper()
        await scraper.start()
        
        category_sites = []
        
        print(f"🎯 {category.upper()} started ({len(queries)} queries)")
        
        for query in queries:
            try:
                results = await scraper.search(query, num_results=sites_per_query)
                
                for r in results:
                    domain = r['domain']
                    
                    async with self.lock:
                        if domain not in self.existing_domains:
                            self.existing_domains.add(domain)
                            r['discovered_via'] = query
                            r['category_hint'] = category
                            category_sites.append(r)
                            self.all_sites.append(r)
                
                await asyncio.sleep(0.2)
                
            except Exception:
                continue
        
        await scraper.stop()
        
        print(f"✅ {category.upper()} complete: +{len(category_sites)} sites")
        
        return category_sites
    
    async def run_parallel(self, sites_per_query: int = 10) -> List[dict]:
        """Run all categories in parallel"""
        
        print("\n" + "=" * 60)
        print("🚀 PARALLEL DISCOVERY")
        print("=" * 60)
        
        tasks = []
        for category, queries in SEARCH_QUERIES.items():
            task = asyncio.create_task(
                self.search_category(category, queries, sites_per_query)
            )
            tasks.append(task)
        
        print(f"📂 {len(tasks)} categories running in parallel...")
        print("-" * 40)
        
        start = datetime.now()
        await asyncio.gather(*tasks, return_exceptions=True)
        duration = (datetime.now() - start).total_seconds()
        
        print("\n" + "-" * 40)
        print(f"⏱️  Parallel discovery: {round(duration, 1)} seconds")
        print(f"📊 Total found: {len(self.all_sites)} sites")
        
        return self.all_sites


class Scanner:
    """Main scanner class"""
    
    def __init__(self, batch_size: int = 20):
        self.batch_size = batch_size
        self.db = None
        self.existing_domains = set()
        self.stats = {
            'discovered': 0,
            'crawled': 0,
            'analyzed': 0,
            'saved': 0,
            'errors': 0
        }
    
    async def initialize(self):
        """Initialize scanner"""
        print("\n" + "=" * 60)
        print("🏴 IMPERIAL AI ARCHIVE SCANNER")
        print("=" * 60)
        
        self.db = SupabaseClient()
        
        # Load existing domains
        existing = self.db.get_all(limit=10000)
        for record in existing:
            domain = record.get('url', '').replace('https://', '').replace('http://', '').split('/')[0]
            self.existing_domains.add(domain)
        
        print(f"📊 Database: {len(existing)} existing records")
        print(f"📦 Batch size: {self.batch_size} sites per API call")
        print("=" * 60)
    
    async def discover(self, target_sites: int = 1000) -> List[dict]:
        """Phase 1: Discovery"""
        
        print(f"\n📍 PHASE 1: DISCOVERY (target: {target_sites})")
        print("-" * 60)
        
        discovery = ParallelDiscovery(self.existing_domains)
        all_sites = await discovery.run_parallel(sites_per_query=10)
        
        # Limit to target
        sites = all_sites[:target_sites]
        
        # Save checkpoint
        save_checkpoint(sites, phase="discovery_complete")
        
        self.stats['discovered'] = len(sites)
        return sites
    
    async def process(self, sites: List[dict], resume_from: int = 0,
                      prior_analyzed: List[dict] = None,
                      prior_errors: List[dict] = None) -> List[dict]:
        """Phase 2 & 3: Crawl + Analyze

        - Only successfully crawled sites are sent to the analyzer, and each
          analysis is matched back to its own site (no index drift).
        - Failed sites are recorded in `errors` (identifiable per batch).
        - After every batch the progress checkpoint is written to disk so an
          interrupted run can resume from `current_index` without re-inserting
          already-saved records.
        """

        if not sites:
            return []

        print(f"\n📍 PHASE 2 & 3: CRAWL + BATCH ANALYZE")
        print(f"📦 Batch size: {self.batch_size}")
        if resume_from:
            print(f"⏩ Resuming from index {resume_from} "
                  f"({len(prior_analyzed or [])} already analyzed, "
                  f"{len(prior_errors or [])} already failed)")
        print("-" * 60)

        crawler = ImperialCrawler()
        await crawler.start()

        analyzer = GeminiAnalyzer(batch_size=self.batch_size)

        # Carry over state from a previous (interrupted) run. These records
        # were already inserted into the DB when their batch finished, so they
        # are kept for the checkpoint/summary only - never re-inserted.
        all_analyzed = list(prior_analyzed or [])
        all_errors = list(prior_errors or [])

        # Process in batches
        for batch_start in range(resume_from, len(sites), self.batch_size):
            batch_end = min(batch_start + self.batch_size, len(sites))
            batch_sites = sites[batch_start:batch_end]

            batch_num = (batch_start // self.batch_size) + 1
            total_batches = (len(sites) + self.batch_size - 1) // self.batch_size

            print(f"\n📦 Batch {batch_num}/{total_batches} ({len(batch_sites)} sites)")

            # 1. Crawl - keep (site, crawl_data) pairs so analysis results
            #    always map back to the correct site
            crawled_pairs = []  # List[Tuple[global_index, site, crawl_data]]
            for i, site in enumerate(batch_sites):
                idx = batch_start + i + 1
                domain = site.get('domain', '')[:30]
                print(f"  [{idx}/{len(sites)}] {domain:30s} ", end="", flush=True)

                try:
                    crawl_data = await crawler.analyze(site['url'])
                    crawl_data['discovery'] = site
                    crawled_pairs.append((batch_start + i, site, crawl_data))
                    self.stats['crawled'] += 1

                    providers = crawl_data.get('network', {}).get('providers_found', [])
                    print(f"✅{' 🎯 ' + str(providers) if providers else ''}")

                except Exception as e:
                    print(f"❌ {type(e).__name__}")
                    self.stats['errors'] += 1
                    all_errors.append({
                        'phase': 'crawl',
                        'batch': batch_num,
                        'index': batch_start + i,
                        'url': site.get('url', ''),
                        'domain': site.get('domain', ''),
                        'error': f"{type(e).__name__}: {e}"
                    })

            # 2. Batch analyze - ONLY the successfully crawled sites
            if crawled_pairs:
                print(f"\n  🤖 Batch analyzing {len(crawled_pairs)} sites...")
                crawl_results = [crawl_data for _, _, crawl_data in crawled_pairs]
                batch_analyses = await analyzer.analyze_batch(crawl_results)

                batch_analyzed = []
                # Zip analyses with the crawled pairs (NOT the raw batch_sites)
                # so discovered_via / category_hint never drift to a wrong site
                for (site_index, site, crawl_data), analysis in zip(crawled_pairs, batch_analyses):
                    if analysis:
                        analysis['discovered_via'] = site.get('discovered_via', '')
                        if not analysis.get('category') or analysis['category'] == 'Other':
                            analysis['category'] = site.get('category_hint', 'Other')

                        batch_analyzed.append(analysis)
                        self.stats['analyzed'] += 1
                    else:
                        self.stats['errors'] += 1
                        all_errors.append({
                            'phase': 'analyze',
                            'batch': batch_num,
                            'index': site_index,
                            'url': site.get('url', ''),
                            'domain': site.get('domain', ''),
                            'error': 'Analysis returned no result'
                        })

                # Save this batch's results immediately
                if batch_analyzed:
                    saved = self.db.batch_insert(batch_analyzed)
                    self.stats['saved'] += saved
                    print(f"  💾 Saved {saved} records")
                    all_analyzed.extend(batch_analyzed)

            # 3. Persist progress after EVERY batch - resume continues from
            #    batch_end, and already-saved records are not re-inserted
            save_progress_checkpoint(all_analyzed, all_errors, batch_end, len(sites))
            print(f"  💾 Progress checkpoint: index {batch_end}/{len(sites)} "
                  f"({len(all_analyzed)} analyzed, {len(all_errors)} errors)")

            await asyncio.sleep(1)

        await crawler.stop()
        return all_analyzed
    
    async def run(self, target_sites: int = 1000, from_checkpoint: bool = False):
        """Run complete scan"""
        
        start_time = datetime.now()
        
        # Discovery or load
        if from_checkpoint:
            sites = load_checkpoint()
            if not sites:
                print("⚠️  No checkpoint, starting discovery...")
                sites = await self.discover(target_sites)
        else:
            sites = await self.discover(target_sites)
        
        if not sites:
            print("❌ No sites to process!")
            return

        # Resume progress (analyzed results + failed sites + next index)
        resume_from = 0
        prior_analyzed = []
        prior_errors = []
        if from_checkpoint:
            progress = load_progress_checkpoint()
            if progress:
                if progress.get('total_sites') == len(sites):
                    resume_from = progress.get('current_index', 0)
                    prior_analyzed = progress.get('analyzed', [])
                    prior_errors = progress.get('errors', [])
                    # Already-saved records count toward the totals but are
                    # NOT re-inserted into the database
                    self.stats['analyzed'] += len(prior_analyzed)
                    self.stats['saved'] += len(prior_analyzed)
                    self.stats['errors'] += len(prior_errors)
                else:
                    print(f"⚠️  Progress checkpoint total ({progress.get('total_sites')}) "
                          f"!= site list ({len(sites)}), starting from scratch")

        if resume_from >= len(sites):
            print("✅ All batches already completed in previous run - nothing to do")
            return

        # Process
        await self.process(sites, resume_from=resume_from,
                           prior_analyzed=prior_analyzed,
                           prior_errors=prior_errors)
        
        # Summary
        duration = (datetime.now() - start_time).total_seconds()
        
        print("\n" + "=" * 60)
        print("✅ SCAN COMPLETE")
        print("=" * 60)
        print(f"  🔍 Discovered:  {self.stats['discovered']:4d}")
        print(f"  🌐 Crawled:     {self.stats['crawled']:4d}")
        print(f"  🤖 Analyzed:    {self.stats['analyzed']:4d}")
        print(f"  💾 Saved:       {self.stats['saved']:4d}")
        print(f"  ❌ Errors:      {self.stats['errors']:4d}")
        print(f"  ⏱️  Time:        {duration/60:.1f} min")
        print("=" * 60)


async def main():
    """Main entry point"""
    
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  🏴 IMPERIAL AI ARCHIVE v3.0                            ║")
    print("║  Deep intelligence scanner for AI websites              ║")
    print("╚══════════════════════════════════════════════════════════╝")
    
    # Check for checkpoint
    checkpoint_exists = os.path.exists(CHECKPOINT_FILE)
    
    if checkpoint_exists:
        print("\n💾 Checkpoint found!")
        choice = input("Resume from checkpoint? (Y/n): ").strip().lower()
        from_checkpoint = choice != 'n'
    else:
        from_checkpoint = False
    
    # Configuration
    if not from_checkpoint:
        target_input = input("\n🎯 Target sites (default 1000): ").strip()
        target_sites = int(target_input) if target_input.isdigit() else 1000
    else:
        target_sites = 0
    
    batch_input = input("📦 Batch size (default 20): ").strip()
    batch_size = int(batch_input) if batch_input.isdigit() else 20
    
    # Run
    scanner = Scanner(batch_size=batch_size)
    
    try:
        await scanner.initialize()
        await scanner.run(target_sites=target_sites, from_checkpoint=from_checkpoint)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted! Progress saved.")
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())