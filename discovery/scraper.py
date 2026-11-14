# discovery/scraper.py
"""DDG Scraper - Fixed for latest duckduckgo-search"""

import asyncio
from typing import Dict, List
from urllib.parse import urlparse


class DDGScraper:
    """DuckDuckGo Scraper - Compatible with all versions"""
    
    SKIP_DOMAINS = {
        'youtube.com', 'twitter.com', 'x.com', 'facebook.com', 
        'instagram.com', 'linkedin.com', 'reddit.com', 'quora.com',
        'medium.com', 'wikipedia.org', 'amazon.com', 'github.com',
        'stackoverflow.com', 'g2.com', 'capterra.com', 'producthunt.com',
        'pinterest.com', 'tiktok.com',
    }
    
    def __init__(self):
        self.ddgs = None
    
    async def start(self):
        print("🦆 DDG Scraper ready")
    
    async def stop(self):
        pass
    
    def _extract_domain(self, url: str) -> str:
        try:
            domain = urlparse(url).netloc.lower()
            return domain[4:] if domain.startswith('www.') else domain
        except:
            return ""
    
    def _should_skip(self, url: str) -> bool:
        domain = self._extract_domain(url)
        return any(skip in domain for skip in self.SKIP_DOMAINS)
    
    async def search(self, query: str, num_results: int = 20) -> List[Dict]:
        """Search using DuckDuckGo"""
        loop = asyncio.get_running_loop()
        
        def do_search():
            results = []
            try:
                # Try new API first (v6+)
                try:
                    from duckduckgo_search import DDGS
                    ddgs = DDGS()
                    ddg_results = ddgs.text(query, max_results=num_results * 2)
                    if ddg_results:
                        results = list(ddg_results)
                except TypeError:
                    # Old API fallback
                    from duckduckgo_search import DDGS
                    with DDGS() as ddgs:
                        ddg_results = ddgs.text(query, max_results=num_results * 2)
                        if ddg_results:
                            results = list(ddg_results)
            except ImportError:
                # Try ddgs package
                try:
                    from ddgs import DDGS
                    with DDGS() as ddgs:
                        ddg_results = ddgs.text(query, max_results=num_results * 2, backend="api")
                        if ddg_results:
                            results = list(ddg_results)
                except Exception as e:
                    print(f"DDG error: {e}")
            except Exception as e:
                print(f"DDG error: {e}")
            
            return results
        
        try:
            raw = await loop.run_in_executor(None, do_search)
            
            formatted = []
            for r in raw:
                url = r.get('href', r.get('link', ''))
                if not url or self._should_skip(url):
                    continue
                
                formatted.append({
                    'url': url,
                    'title': r.get('title', ''),
                    'snippet': r.get('body', r.get('snippet', '')),
                    'domain': self._extract_domain(url),
                })
                
                if len(formatted) >= num_results:
                    break
            
            return formatted
            
        except Exception as e:
            print(f"Search error: {e}")
            return []