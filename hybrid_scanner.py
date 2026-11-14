# run_hybrid_scan.py
"""
🔥 HYBRID SCANNER - Best of Both Worlds
- Layer 1: Pattern Matching (fast, free, 70-80% cases)
- Layer 2: AI Verification (slow, paid, 20-30% cases)
"""

import asyncio
from datetime import datetime
from typing import List, Dict

from discovery.ddg_scraper import DDGScraper
from discovery.search_queries import SEARCH_QUERIES
from crawler.engine import ImperialCrawler
from analyzer.deep_analyzer import DeepAnalyzer
from analyzer.gemini_analyzer import GeminiAnalyzer
from db.supabase_client import SupabaseClient


class HybridScanner:
    """
    Hybrid approach:
    1. Always use pattern matching first (free)
    2. Only use AI for unclear cases (cost-effective)
    """
    
    def __init__(self, ai_threshold: int = 70):
        self.ai_threshold = ai_threshold  # Only use AI if confidence < this
        self.pattern_analyzer = DeepAnalyzer()
        self.ai_analyzer = None  # Lazy load
        self.db = None
        self.stats = {
            'total': 0,
            'pattern_only': 0,
            'ai_verified': 0,
            'saved': 0
        }
    
    async def initialize(self):
        print("🔥 HYBRID SCANNER - Pattern Matching + AI Fallback")
        print("=" * 60)
        
        self.db = SupabaseClient()
        existing = self.db.get_all(limit=5000)
        
        print(f"📊 Database: {len(existing)} existing records")
        print(f"🎯 AI threshold: {self.ai_threshold}% confidence")
        print("=" * 60)
        
        return set(
            r.get('url', '').replace('https://', '').replace('http://', '').split('/')[0]
            for r in existing
        )
    
    async def scan(self, target_sites: int = 500):
        """Main scan process"""
        
        existing_domains = await self.initialize()
        
        # 1. DISCOVERY
        print(f"\n📍 PHASE 1: Discovery (target: {target_sites})")
        print("-" * 60)
        
        sites = await self._discover(existing_domains, target_sites)
        
        if not sites:
            print("❌ No sites found!")
            return
        
        print(f"✅ Found {len(sites)} new sites")
        
        # 2. ANALYZE
        print(f"\n📍 PHASE 2: Hybrid Analysis")
        print("-" * 60)
        
        results = await self._analyze_batch(sites)
        
        # 3. SAVE
        print(f"\n📍 PHASE 3: Save to Database")
        print("-" * 60)
        
        saved = self.db.batch_insert(results)
        self.stats['saved'] = saved
        
        # 4. SUMMARY
        self._print_summary()
    
    async def _discover(self, existing_domains: set, target: int) -> List[Dict]:
        """Fast parallel discovery"""
        
        scraper = DDGScraper()
        await scraper.start()
        
        sites = []
        
        # Quick scan: 5 sites per query, stop when target reached
        for cat, queries in SEARCH_QUERIES.items():
            if len(sites) >= target:
                break
            
            for query in queries[:3]:  # Only first 3 queries per category
                if len(sites) >= target:
                    break
                
                results = await scraper.search(query, num_results=5)
                
                new = 0
                for r in results:
                    if r['domain'] not in existing_domains:
                        existing_domains.add(r['domain'])
                        r['category_hint'] = cat
                        r['discovered_via'] = query
                        sites.append(r)
                        new += 1
                
                if new > 0:
                    print(f"  {query[:35]:35s} +{new}")
                
                await asyncio.sleep(0.2)
        
        await scraper.stop()
        return sites[:target]
    
    async def _analyze_batch(self, sites: List[Dict]) -> List[Dict]:
        """Hybrid analysis: Pattern first, AI fallback"""
        
        crawler = ImperialCrawler()
        await crawler.start()
        
        results = []
        ai_queue = []  # Sites that need AI verification
        
        # PHASE 1: Pattern matching (FAST)
        print("\n🔍 Phase 2a: Pattern Matching Analysis")
        print("-" * 40)
        
        for i, site in enumerate(sites):
            domain = site['domain'][:30]
            print(f"[{i+1:3d}/{len(sites)}] {domain:30s} ", end="", flush=True)
            
            try:
                # Crawl
                crawl_data = await crawler.analyze(site['url'])
                crawl_data['discovery'] = site
                
                # Pattern analysis
                analysis = self.pattern_analyzer.analyze(crawl_data)
                
                confidence = analysis.get('confidence', 0)
                status = analysis.get('status', 'unknown')
                
                # Decide: Good enough or needs AI?
                if confidence >= self.ai_threshold or status == 'verified':
                    # Pattern analysis is confident!
                    analysis['analysis_method'] = 'pattern_matching'
                    analysis['discovered_via'] = site.get('discovered_via', '')
                    
                    if not analysis.get('category') or analysis['category'] == 'Other':
                        analysis['category'] = site.get('category_hint', 'Other')
                    
                    results.append(analysis)
                    self.stats['pattern_only'] += 1
                    
                    emoji = "✅" if status == "verified" else "⚠️"
                    print(f"{emoji} {status:10s} C:{confidence:3d}% [PATTERN]")
                else:
                    # Needs AI verification
                    ai_queue.append((crawl_data, site, analysis))
                    print(f"❓ {status:10s} C:{confidence:3d}% [QUEUE AI]")
                
                self.stats['total'] += 1
                
            except Exception as e:
                print(f"❌ {type(e).__name__}")
            
            await asyncio.sleep(0.5)
        
        await crawler.stop()
        
        # PHASE 2: AI verification (SLOW, only if needed)
        if ai_queue:
            print(f"\n🤖 Phase 2b: AI Verification ({len(ai_queue)} sites)")
            print("-" * 40)
            print(f"💡 Saved {len(results)} sites from AI analysis (${len(results)*0.01:.2f})")
            
            if not self.ai_analyzer:
                self.ai_analyzer = GeminiAnalyzer()
            
            for i, (crawl_data, site, pattern_result) in enumerate(ai_queue):
                domain = site['domain'][:30]
                print(f"[{i+1:3d}/{len(ai_queue)}] {domain:30s} ", end="", flush=True)
                
                try:
                    analysis = await self.ai_analyzer.analyze(crawl_data)
                    
                    if analysis:
                        analysis['analysis_method'] = 'ai_verified'
                        analysis['pattern_confidence'] = pattern_result.get('confidence', 0)
                        analysis['discovered_via'] = site.get('discovered_via', '')
                        
                        if not analysis.get('category') or analysis['category'] == 'Other':
                            analysis['category'] = site.get('category_hint', 'Other')
                        
                        results.append(analysis)
                        self.stats['ai_verified'] += 1
                        
                        status = analysis.get('status', 'unknown')
                        score = analysis.get('trust_score', 0)
                        emoji = "✅" if status == "verified" else "⚠️"
                        print(f"{emoji} {status:10s} S:{score:3d} [AI]")
                    else:
                        # AI failed, use pattern result
                        pattern_result['analysis_method'] = 'pattern_fallback'
                        results.append(pattern_result)
                        print("⏩ [PATTERN FALLBACK]")
                    
                except Exception as e:
                    # AI error, use pattern result
                    pattern_result['analysis_method'] = 'pattern_fallback'
                    results.append(pattern_result)
                    print(f"⏩ {type(e).__name__} [FALLBACK]")
                
                await asyncio.sleep(2)
        
        return results
    
    def _print_summary(self):
        """Print final summary"""
        
        print("\n" + "=" * 60)
        print("📊 HYBRID SCAN COMPLETE")
        print("=" * 60)
        
        print(f"\n📈 ANALYSIS BREAKDOWN:")
        print(f"  Total sites:        {self.stats['total']:4d}")
        print(f"  Pattern only:       {self.stats['pattern_only']:4d} ({self.stats['pattern_only']/max(1,self.stats['total'])*100:.1f}%)")
        print(f"  AI verified:        {self.stats['ai_verified']:4d} ({self.stats['ai_verified']/max(1,self.stats['total'])*100:.1f}%)")
        print(f"  Saved to DB:        {self.stats['saved']:4d}")
        
        if self.stats['ai_verified'] > 0:
            print(f"\n💰 COST ANALYSIS:")
            pattern_ratio = self.stats['pattern_only'] / max(1, self.stats['total'])
            savings = self.stats['pattern_only'] * 0.01
            cost = self.stats['ai_verified'] * 0.01
            print(f"  Pattern matching saved: ${savings:.2f}")
            print(f"  AI API cost:            ${cost:.2f}")
            print(f"  Total cost:             ${cost:.2f}")
            print(f"  Efficiency:             {pattern_ratio*100:.1f}% free analysis")
        
        print("\n" + "=" * 60)


async def main():
    print("\n🔥 HYBRID SCANNER")
    print("Combines pattern matching + AI for optimal speed/cost")
    print()
    
    # Config
    target = input("🎯 Target sites (default 200): ").strip()
    target = int(target) if target.isdigit() else 200
    
    threshold = input("🤖 AI confidence threshold (default 70): ").strip()
    threshold = int(threshold) if threshold.isdigit() else 70
    
    # Run
    scanner = HybridScanner(ai_threshold=threshold)
    
    try:
        start = datetime.now()
        await scanner.scan(target_sites=target)
        duration = (datetime.now() - start).total_seconds()
        
        print(f"\n⏱️  Total time: {duration/60:.1f} minutes")
        print(f"🚀 Speed: {target/(duration/60):.1f} sites/min")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
