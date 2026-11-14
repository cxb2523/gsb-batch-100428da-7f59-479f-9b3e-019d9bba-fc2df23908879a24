"""
DuckDuckGo Scraper - CAPTCHA yok, hızlı, güvenli
"""

import asyncio
from typing import Dict, List
from urllib.parse import urlparse
from ddgs import DDGS  # YENİ



class DDGScraper:
    """
    DuckDuckGo ile arama - Google'a alternatif
    
    Avantajları:
    - CAPTCHA yok
    - Browser açmaya gerek yok
    - Hızlı
    - Ücretsiz
    """
    
    # Atlanacak siteler (blog, forum, vs.)
    SKIP_DOMAINS = {
        'youtube.com', 'youtu.be', 'twitter.com', 'x.com',
        'facebook.com', 'instagram.com', 'linkedin.com',
        'reddit.com', 'quora.com', 'pinterest.com',
        'medium.com', 'forbes.com', 'techcrunch.com',
        'wikipedia.org', 'amazon.com', 'ebay.com',
        'g2.com', 'capterra.com', 'producthunt.com',
        'github.com', 'stackoverflow.com',
    }
    
    def __init__(self):
        self.ddgs = None
        
    async def __aenter__(self):
        await self.start()
        return self
        
    async def __aexit__(self, *args):
        await self.stop()
        
    async def start(self):
        """Başlat"""
        print("🦆 DuckDuckGo Scraper ready")
        
    async def stop(self):
        """Durdur"""
        print("🛑 DDG Scraper stopped")
    
    def _extract_domain(self, url: str) -> str:
        """URL'den domain çıkar"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain
        except:
            return ""
    
    def _should_skip(self, url: str) -> bool:
        """Bu URL atlanmalı mı?"""
        domain = self._extract_domain(url)
        for skip in self.SKIP_DOMAINS:
            if skip in domain:
                return True
        return False
    
    async def search(
        self, 
        query: str, 
        num_results: int = 20,
        pages: int = 1,  # Uyumluluk için
    ) -> List[Dict]:
        """
        DuckDuckGo'da arama yap
        
        Args:
            query: Arama terimi
            num_results: İstenen sonuç sayısı
            
        Returns:
            List of {url, title, snippet, domain, position}
        """
        print(f"🔍 DDG Searching: '{query}'")
        
        loop = asyncio.get_running_loop()
        
        def do_search():
            results = []
            try:
                with DDGS() as ddgs:
                    # Text search
                    ddg_results = ddgs.text(
                        query,
                        max_results=num_results * 2,  # Fazla al, filtrele
                    )
                    
                    if ddg_results:
                        for r in ddg_results:
                            results.append(r)
                            
            except Exception as e:
                print(f"❌ DDG error: {e}")
                
            return results
        
        try:
            # Async wrapper (DDGS senkron çalışır)
            raw_results = await loop.run_in_executor(None, do_search)
            
            # Formatla ve filtrele
            formatted = []
            position = 0
            
            for r in raw_results:
                url = r.get('href', '')
                
                # Boş URL atla
                if not url:
                    continue
                    
                # Skip domains
                if self._should_skip(url):
                    continue
                
                position += 1
                
                formatted.append({
                    'url': url,
                    'title': r.get('title', ''),
                    'snippet': r.get('body', ''),
                    'domain': self._extract_domain(url),
                    'position': position,
                })
                
                # Yeterli sonuç varsa dur
                if len(formatted) >= num_results:
                    break
            
            print(f"✅ Found {len(formatted)} valid results")
            return formatted
            
        except Exception as e:
            print(f"❌ Search error: {e}")
            return []
    
    async def search_multiple(
        self,
        queries: List[str],
        results_per_query: int = 15,
        delay: float = 1.0,
    ) -> Dict[str, List[Dict]]:
        """Birden fazla sorgu için arama"""
        
        all_results = {}
        
        for i, query in enumerate(queries):
            print(f"\n[{i+1}/{len(queries)}] {query}")
            
            results = await self.search(query, num_results=results_per_query)
            all_results[query] = results
            
            # Rate limiting
            if i < len(queries) - 1:
                await asyncio.sleep(delay)
        
        return all_results