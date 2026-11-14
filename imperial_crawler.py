# imperial_crawler.py
"""
🏴 IMPERIAL AI CRAWLER - Complete System
Discovery → Crawl → AI Detection → Gemini Analysis → Database
"""

import asyncio
import re
import os
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from playwright.async_api import async_playwright, Page, Request, Response
import google.generativeai as genai
from dotenv import load_dotenv
import json

load_dotenv()


# ============================================================
# 1. AI DETECTION ENGINE
# ============================================================

@dataclass
class AIDetection:
    """Tespit edilen AI bilgileri"""
    providers: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    api_calls: List[Dict] = field(default_factory=list)
    confidence_score: int = 0
    evidence: List[str] = field(default_factory=list)


class AIDetector:
    """AI tespit motoru"""
    
    PROVIDERS = {
        'openai': {
            'domains': [r'api\.openai\.com', r'openai\.com/api'],
            'keywords': ['openai', 'chatgpt'],
            'models': [r'gpt-4[o\-\w]*', r'gpt-3\.5-turbo[\w\-]*', r'dall-e-[\d]+']
        },
        'anthropic': {
            'domains': [r'api\.anthropic\.com', r'claude\.ai/api'],
            'keywords': ['anthropic', 'claude'],
            'models': [r'claude-3[\w\-]+', r'claude-2[\w\-]*', r'claude-instant']
        },
        'google': {
            'domains': [r'generativelanguage\.googleapis\.com', r'aiplatform\.googleapis\.com'],
            'keywords': ['gemini', 'palm', 'bard'],
            'models': [r'gemini-[\w\-]+', r'palm-[\d]+', r'text-bison']
        },
        'replicate': {
            'domains': [r'api\.replicate\.com'],
            'keywords': ['replicate'],
            'models': [r'stable-diffusion[\w\-]*', r'llama-[\d]+[\w\-]*']
        },
        'huggingface': {
            'domains': [r'api-inference\.huggingface\.co'],
            'keywords': ['huggingface', 'transformers'],
            'models': [r'mistral[\w\-]*', r'mixtral[\w\-]*']
        },
        'cohere': {
            'domains': [r'api\.cohere\.ai'],
            'keywords': ['cohere'],
            'models': [r'command[\w\-]*', r'embed[\w\-]*']
        },
        'groq': {
            'domains': [r'api\.groq\.com'],
            'keywords': ['groq'],
            'models': [r'llama-[\d]+[\w\-]*', r'mixtral[\w\-]*']
        },
        'mistral': {
            'domains': [r'api\.mistral\.ai'],
            'keywords': ['mistral'],
            'models': [r'mistral-[\w\-]+', r'mixtral-[\w\-]+']
        }
    }
    
    def __init__(self):
        self.detection = AIDetection()
        self.network_log = []
    
    async def scan_website(self, url: str, timeout: int = 30) -> Dict:
        """
        Website'i tara ve AI bilgilerini topla
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'
            )
            
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = {runtime: {}};
            """)
            
            page = await context.new_page()
            page.on('request', self._handle_request)
            page.on('response', self._handle_response)
            
            title = ""
            page_text = ""
            status_code = 0
            
            try:
                response = await page.goto(url, wait_until='networkidle', timeout=timeout * 1000)
                status_code = response.status if response else 0
                
                await asyncio.sleep(3)
                
                title = await page.title()
                page_text = await page.evaluate("() => document.body.innerText.substring(0, 3000)")
                
                await self._scan_javascript(page)
                await self._scan_page_content(page)
                self._calculate_confidence()
                
            except Exception as e:
                print(f"  ⚠️ Crawl error: {str(e)[:50]}")
            
            finally:
                await browser.close()
        
        return {
            'url': url,
            'title': title,
            'status_code': status_code,
            'page_text': page_text,
            'detection': {
                'providers': self.detection.providers,
                'models': self.detection.models,
                'api_calls': self.detection.api_calls,
                'confidence_score': self.detection.confidence_score,
                'evidence': self.detection.evidence
            }
        }
    
    def _handle_request(self, request: Request):
        """Network request yakala"""
        url = request.url
        method = request.method
        
        if method in ['POST', 'PUT', 'PATCH']:
            for provider, patterns in self.PROVIDERS.items():
                for domain_pattern in patterns['domains']:
                    if re.search(domain_pattern, url, re.IGNORECASE):
                        if provider not in self.detection.providers:
                            self.detection.providers.append(provider)
                            self.detection.evidence.append(f"API: {url[:60]}")
                        
                        self.detection.api_calls.append({
                            'provider': provider,
                            'url': url,
                            'method': method
                        })
                        
                        try:
                            post_data = request.post_data
                            if post_data:
                                self._extract_models(post_data, provider)
                        except:
                            pass
    
    async def _handle_response(self, response: Response):
        """Network response yakala"""
        try:
            content_type = response.headers.get('content-type', '')
            if 'json' in content_type and response.status == 200:
                body = await response.text()
                for provider, patterns in self.PROVIDERS.items():
                    if provider in self.detection.providers:
                        for model_pattern in patterns['models']:
                            matches = re.findall(model_pattern, body, re.IGNORECASE)
                            for match in matches:
                                if match not in self.detection.models:
                                    self.detection.models.append(match)
        except:
            pass
    
    async def _scan_javascript(self, page: Page):
        """JavaScript tara"""
        try:
            js_content = await page.evaluate("""
                () => {
                    const scripts = Array.from(document.querySelectorAll('script:not([src])'));
                    return scripts.map(s => s.textContent).join('\\n');
                }
            """)
            
            for provider, patterns in self.PROVIDERS.items():
                for keyword in patterns['keywords']:
                    if keyword.lower() in js_content.lower():
                        if provider not in self.detection.providers:
                            self.detection.providers.append(provider)
                            self.detection.evidence.append(f"JS: {keyword}")
        except:
            pass
    
    async def _scan_page_content(self, page: Page):
        """Sayfa içeriği tara"""
        try:
            text = await page.evaluate("() => document.body.innerText")
            all_model_patterns = []
            for patterns in self.PROVIDERS.values():
                all_model_patterns.extend(patterns['models'])
            
            for pattern in all_model_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    if match not in self.detection.models:
                        self.detection.models.append(match)
        except:
            pass
    
    def _extract_models(self, text: str, provider: str):
        """Model isimlerini çıkar"""
        patterns = self.PROVIDERS.get(provider, {}).get('models', [])
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if match not in self.detection.models:
                    self.detection.models.append(match)
    
    def _calculate_confidence(self):
        """Güven skoru hesapla"""
        score = 0
        if self.detection.api_calls:
            score += 50
        if self.detection.models:
            score += 30
        if self.detection.providers:
            score += 10 * min(len(self.detection.providers), 2)
        score += min(len(self.detection.evidence) * 5, 20)
        self.detection.confidence_score = min(score, 100)


# ============================================================
# 2. GEMINI ANALYZER
# ============================================================

class GeminiAnalyzer:
    """Gemini ile site analizi"""
    
    PROMPT = """Analyze this AI website and return ONLY valid JSON.

URL: {url}
Title: {title}
Detected Providers: {providers}
Detected Models: {models}
Confidence: {confidence}/100
Page Content: {content}

Return format:
{{
  "category": "Chatbot|Image Generation|Writing|Code|Audio|Video|Productivity|Search|Data Analysis|Design|Other",
  "status": "verified|suspicious|fake",
  "trust_score": 0-100,
  "marketing_claim": "One sentence about what they claim",
  "actual_tech": "One sentence about what they actually use",
  "detected_arsenal": [{{"model": "gpt-4", "type": "openai"}}],
  "capabilities": ["CHATBOT", "TEXT-TO-IMAGE", "CODE-GENERATION"],
  "pricing": {{"type": "free|freemium|paid", "details": "description"}},
  "evidence_logs": ["Finding 1", "Finding 2"]
}}

ONLY return the JSON, no markdown, no explanations."""

    def __init__(self):
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEY bulunamadı!")
        
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
    
    async def analyze(self, crawl_data: Dict) -> Optional[Dict]:
        """Site'i analiz et"""
        try:
            url = crawl_data.get('url', '')
            title = crawl_data.get('title', '')
            detection = crawl_data.get('detection', {})
            page_text = crawl_data.get('page_text', '')[:1500]
            
            providers = detection.get('providers', [])
            models = detection.get('models', [])
            confidence = detection.get('confidence_score', 0)
            
            prompt = self.PROMPT.format(
                url=url,
                title=title,
                providers=', '.join(providers) if providers else 'None',
                models=', '.join(models) if models else 'None',
                confidence=confidence,
                content=page_text[:800]
            )
            
            response = await asyncio.to_thread(
                self.model.generate_content,
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=1500
                )
            )
            
            text = response.text
            
            # JSON çıkar
            if '```json' in text:
                start = text.find('```json') + 7
                end = text.find('```', start)
                text = text[start:end].strip()
            elif '{' in text:
                start = text.find('{')
                end = text.rfind('}') + 1
                text = text[start:end]
            
            analysis = json.loads(text)
            
            # Metadata ekle
            analysis['url'] = url
            analysis['title'] = title
            analysis['domain'] = url.replace('https://', '').replace('http://', '').split('/')[0]
            analysis['detection_confidence'] = confidence
            analysis['detected_providers'] = providers
            analysis['detected_models'] = models
            
            return analysis
            
        except Exception as e:
            print(f"  ❌ Gemini error: {str(e)[:50]}")
            return None


# ============================================================
# 3. SUPABASE DATABASE
# ============================================================

class SupabaseDB:
    """Supabase veritabanı"""
    
    def __init__(self):
        from supabase import create_client
        
        url = os.getenv('SUPABASE_URL')
        key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        
        if not url or not key:
            raise ValueError("SUPABASE_URL ve SUPABASE_SERVICE_ROLE_KEY gerekli!")
        
        self.client = create_client(url, key)
    
    def save_record(self, analysis: Dict) -> bool:
        """Analizi kaydet"""
        try:
            # Prepare data
            data = {
                'name': analysis.get('title', 'Unknown')[:255],
                'url': analysis.get('url'),
                'trust_score': analysis.get('trust_score', 50),
                'status': analysis.get('status', 'suspicious'),
                'marketing_claim': analysis.get('marketing_claim', '')[:1000],
                'detected_arsenal': analysis.get('detected_arsenal', []),
                'capabilities': analysis.get('capabilities', []),
                'pricing': analysis.get('pricing', {}),
                'evidence_logs': analysis.get('evidence_logs', []),
                'category': analysis.get('category', 'Other'),
                'last_verified': datetime.now().isoformat()
            }
            
            # Upsert
            response = self.client.table('intelligence_records').upsert(
                data,
                on_conflict='url'
            ).execute()
            
            return len(response.data) > 0
            
        except Exception as e:
            print(f"  ❌ DB error: {str(e)[:50]}")
            return False
    
    def get_stats(self) -> Dict:
        """İstatistikler"""
        try:
            response = self.client.table('intelligence_records').select('status').execute()
            records = response.data
            
            return {
                'total': len(records),
                'verified': sum(1 for r in records if r.get('status') == 'verified'),
                'suspicious': sum(1 for r in records if r.get('status') == 'suspicious'),
                'fake': sum(1 for r in records if r.get('status') == 'fake')
            }
        except:
            return {'total': 0}


# ============================================================
# 4. DISCOVERY ENGINE
# ============================================================

class DiscoveryEngine:
    """DuckDuckGo ile site keşfi"""
    
    SEARCH_QUERIES = {
        'chatbot': ['ai chatbot free', 'chatgpt alternative', 'ai assistant'],
        'image': ['ai image generator', 'stable diffusion online', 'dall-e alternative'],
        'writing': ['ai writing assistant', 'ai copywriter', 'grammarly alternative'],
        'code': ['ai code assistant', 'github copilot alternative', 'ai programmer']
    }
    
    async def discover(self, category: str, max_sites: int = 10) -> List[str]:
        """Kategori için siteleri keşfet"""
        from duckduckgo_search import DDGS
        
        queries = self.SEARCH_QUERIES.get(category, [])
        urls = set()
        
        try:
            with DDGS() as ddgs:
                for query in queries:
                    results = ddgs.text(query, max_results=max_sites)
                    for r in results:
                        url = r.get('href', '')
                        if url and 'http' in url:
                            urls.add(url)
                    
                    if len(urls) >= max_sites:
                        break
                    
                    await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Discovery error: {e}")
        
        return list(urls)[:max_sites]


# ============================================================
# 5. IMPERIAL CRAWLER - MAIN SYSTEM
# ============================================================

class ImperialCrawler:
    """Complete crawler system"""
    
    def __init__(self):
        self.detector = None
        self.analyzer = GeminiAnalyzer()
        self.db = SupabaseDB()
        self.stats = {
            'crawled': 0,
            'analyzed': 0,
            'saved': 0,
            'errors': 0
        }
    
    async def crawl_and_save(self, url: str) -> bool:
        """
        Tek site: Crawl → Detect → Analyze → Save
        """
        print(f"\n{'='*60}")
        print(f"🔍 Processing: {url}")
        print(f"{'='*60}")
        
        try:
            # 1. CRAWL & DETECT
            print("📡 Crawling & detecting AI...")
            self.detector = AIDetector()
            crawl_data = await self.detector.scan_website(url)
            self.stats['crawled'] += 1
            
            detection = crawl_data.get('detection', {})
            providers = detection.get('providers', [])
            confidence = detection.get('confidence_score', 0)
            
            if providers:
                print(f"  ✅ Detected: {', '.join(providers)} (Confidence: {confidence}/100)")
            else:
                print(f"  ⚠️  No AI detected (Confidence: {confidence}/100)")
            
            # 2. GEMINI ANALYSIS
            print("🤖 Analyzing with Gemini...")
            analysis = await self.analyzer.analyze(crawl_data)
            
            if not analysis:
                print("  ❌ Analysis failed")
                self.stats['errors'] += 1
                return False
            
            self.stats['analyzed'] += 1
            
            status = analysis.get('status', 'unknown')
            score = analysis.get('trust_score', 0)
            category = analysis.get('category', 'Unknown')
            
            print(f"  ✅ {status.upper()} | Score: {score}/100 | Category: {category}")
            
            # 3. SAVE TO DATABASE
            print("💾 Saving to database...")
            saved = self.db.save_record(analysis)
            
            if saved:
                print("  ✅ Saved successfully")
                self.stats['saved'] += 1
                return True
            else:
                print("  ❌ Save failed")
                self.stats['errors'] += 1
                return False
            
        except Exception as e:
            print(f"  ❌ Error: {str(e)[:60]}")
            self.stats['errors'] += 1
            return False
    
    async def batch_crawl(self, urls: List[str]) -> Dict:
        """Birden fazla site işle"""
        print(f"\n{'='*60}")
        print(f"🏴 IMPERIAL CRAWLER - BATCH MODE")
        print(f"{'='*60}")
        print(f"Sites to process: {len(urls)}\n")
        
        start_time = datetime.now()
        
        for i, url in enumerate(urls, 1):
            print(f"\n[{i}/{len(urls)}] {url}")
            await self.crawl_and_save(url)
            await asyncio.sleep(2)  # Rate limit
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # Summary
        print(f"\n{'='*60}")
        print("📊 BATCH COMPLETE")
        print(f"{'='*60}")
        print(f"  Crawled:   {self.stats['crawled']}")
        print(f"  Analyzed:  {self.stats['analyzed']}")
        print(f"  Saved:     {self.stats['saved']}")
        print(f"  Errors:    {self.stats['errors']}")
        print(f"  Duration:  {duration:.1f}s")
        
        db_stats = self.db.get_stats()
        print(f"\n💾 Database Total: {db_stats['total']}")
        print(f"  ✅ Verified:   {db_stats['verified']}")
        print(f"  ⚠️  Suspicious: {db_stats['suspicious']}")
        print(f"  ❌ Fake:       {db_stats['fake']}")
        
        return self.stats
    
    async def auto_discover_and_crawl(self, category: str = 'chatbot', max_sites: int = 5):
        """Otomatik keşif ve tarama"""
        print(f"\n{'='*60}")
        print(f"🏴 AUTO DISCOVERY + CRAWL")
        print(f"{'='*60}")
        print(f"Category: {category}")
        print(f"Max sites: {max_sites}\n")
        
        # Discovery
        print("🔍 Discovering sites...")
        discovery = DiscoveryEngine()
        urls = await discovery.discover(category, max_sites)
        
        print(f"✅ Found {len(urls)} sites\n")
        
        # Crawl
        if urls:
            await self.batch_crawl(urls)


# ============================================================
# USAGE EXAMPLES
# ============================================================

async def main():
    """Main function"""
    
    print("\n🏴 IMPERIAL AI CRAWLER")
    print("="*60)
    print("1. Single site crawl")
    print("2. Batch crawl (custom URLs)")
    print("3. Auto discover + crawl")
    
    choice = input("\nSelect mode (1-3): ").strip() or "1"
    
    crawler = ImperialCrawler()
    
    if choice == "1":
        # Single site
        url = input("Enter URL: ").strip() or "https://chat.openai.com"
        await crawler.crawl_and_save(url)
    
    elif choice == "2":
        # Batch
        urls = [
            'https://chat.openai.com',
            'https://claude.ai',
            'https://gemini.google.com'
        ]
        await crawler.batch_crawl(urls)
    
    elif choice == "3":
        # Auto discover
        category = input("Category (chatbot/image/writing/code): ").strip() or "chatbot"
        max_sites = int(input("Max sites (default 5): ").strip() or "5")
        await crawler.auto_discover_and_crawl(category, max_sites)


if __name__ == "__main__":
    asyncio.run(main())