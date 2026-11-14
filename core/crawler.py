# core/crawler.py
"""
🔥 ULTRA CRAWLER - Maximum Data Extraction
Her şeyi toplar: Network, JS, DOM, Cookies, Storage
"""

import asyncio
import re
import json
from typing import Dict, List, Set, Optional, Any
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright, Browser, Page, Request, Response


class UltraCrawler:
    """
    Derin site analizi için ultra crawler.
    Topladığı veriler:
    - Network traffic (XHR, Fetch, WebSocket)
    - JavaScript dosyaları
    - Inline scripts
    - DOM içeriği
    - Meta tags
    - Cookies
    - LocalStorage
    - Headers
    - Console logs
    """
    
    def __init__(self, timeout: int = 25000):
        self.timeout = timeout
        self.browser: Optional[Browser] = None
        self.playwright = None
    
    async def start(self):
        """Browser başlat"""
        print("🚀 Ultra Crawler starting...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-gpu',
                '--disable-web-security',  # CORS bypass
                '--disable-features=IsolateOrigins,site-per-process',
            ]
        )
        print("✅ Ultra Crawler ready")
    
    async def stop(self):
        """Browser kapat"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, *args):
        await self.stop()
    
    async def crawl(self, url: str) -> Dict[str, Any]:
        """
        Siteyi derin analiz et ve tüm veriyi topla
        """
        domain = urlparse(url).netloc.replace('www.', '')
        
        # Veri toplama yapıları
        collected = {
            'url': url,
            'domain': domain,
            'network': {
                'requests': [],
                'api_calls': [],
                'websockets': [],
                'external_scripts': [],
            },
            'content': {
                'title': '',
                'meta_description': '',
                'meta_keywords': '',
                'og_tags': {},
                'page_text': '',
                'headings': [],
            },
            'scripts': {
                'inline': [],
                'external_urls': [],
                'detected_libraries': [],
            },
            'storage': {
                'cookies': [],
                'local_storage': {},
                'session_storage': {},
            },
            'forms': [],
            'links': {
                'internal': [],
                'external': [],
                'pricing_pages': [],
            },
            'errors': [],
            'console_logs': [],
        }
        
        context = None
        
        try:
            # Context oluştur
            context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            
            page = await context.new_page()
            
            # Event listeners ekle
            self._attach_listeners(page, collected)
            
            # Sayfaya git
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=self.timeout)
                await asyncio.sleep(3)  # Dinamik içerik için bekle
            except Exception as e:
                collected['errors'].append(f"Navigation: {str(e)[:100]}")
            
            # Veri çıkar
            await self._extract_content(page, collected)
            await self._extract_scripts(page, collected)
            await self._extract_storage(page, context, collected)
            await self._extract_forms(page, collected)
            await self._extract_links(page, collected, url)
            
            # Pricing sayfasını bul ve tara
            pricing_url = await self._find_pricing_page(page, collected, url)
            if pricing_url:
                collected['pricing_page_data'] = await self._crawl_pricing_page(context, pricing_url)
            
            collected['success'] = True
            
        except Exception as e:
            collected['success'] = False
            collected['errors'].append(f"Crawl error: {str(e)[:200]}")
        
        finally:
            if context:
                await context.close()
        
        return collected
    
    def _attach_listeners(self, page: Page, collected: Dict):
        """Event listener'ları ekle"""
        
        # Request listener
        def on_request(request: Request):
            req_data = {
                'url': request.url[:500],
                'method': request.method,
                'type': request.resource_type,
                'headers': dict(request.headers),
            }
            collected['network']['requests'].append(req_data)
            
            # External script
            if request.resource_type == 'script':
                collected['scripts']['external_urls'].append(request.url)
        
        # Response listener
        async def on_response(response: Response):
            try:
                url = response.url
                status = response.status
                content_type = response.headers.get('content-type', '')
                
                # API response analizi
                if status == 200 and 'json' in content_type:
                    try:
                        body = await response.text()
                        if len(body) < 100000:  # 100KB limit
                            collected['network']['api_calls'].append({
                                'url': url[:300],
                                'body_preview': body[:1000],
                            })
                    except:
                        pass
                
                # JavaScript dosyası analizi
                if 'javascript' in content_type or url.endswith('.js'):
                    try:
                        js_code = await response.text()
                        if len(js_code) < 500000:  # 500KB limit
                            collected['scripts']['inline'].append({
                                'url': url[:200],
                                'code': js_code[:50000],  # İlk 50KB
                            })
                    except:
                        pass
                        
            except Exception as e:
                pass
        
        # Console listener
        def on_console(msg):
            collected['console_logs'].append({
                'type': msg.type,
                'text': msg.text[:500],
            })
        
        # WebSocket listener
        def on_websocket(ws):
            collected['network']['websockets'].append(ws.url)
        
        page.on('request', on_request)
        page.on('response', lambda r: asyncio.create_task(on_response(r)))
        page.on('console', on_console)
        page.on('websocket', on_websocket)
    
    async def _extract_content(self, page: Page, collected: Dict):
        """Sayfa içeriğini çıkar"""
        try:
            # Title
            collected['content']['title'] = await page.title() or ''
            
            # Meta tags
            collected['content']['meta_description'] = await page.evaluate('''() => {
                const meta = document.querySelector('meta[name="description"]');
                return meta ? meta.content : '';
            }''') or ''
            
            collected['content']['meta_keywords'] = await page.evaluate('''() => {
                const meta = document.querySelector('meta[name="keywords"]');
                return meta ? meta.content : '';
            }''') or ''
            
            # OG tags
            collected['content']['og_tags'] = await page.evaluate('''() => {
                const og = {};
                document.querySelectorAll('meta[property^="og:"]').forEach(m => {
                    og[m.getAttribute('property')] = m.content;
                });
                return og;
            }''') or {}
            
            # Page text
            collected['content']['page_text'] = await page.evaluate('''() => {
                const clone = document.body.cloneNode(true);
                clone.querySelectorAll('script, style, noscript, iframe').forEach(el => el.remove());
                return clone.innerText || '';
            }''') or ''
            
            # Headings
            collected['content']['headings'] = await page.evaluate('''() => {
                const headings = [];
                document.querySelectorAll('h1, h2, h3').forEach(h => {
                    headings.push({tag: h.tagName, text: h.innerText.substring(0, 200)});
                });
                return headings.slice(0, 20);
            }''') or []
            
        except Exception as e:
            collected['errors'].append(f"Content extraction: {str(e)[:100]}")
    
    async def _extract_scripts(self, page: Page, collected: Dict):
        """Script'leri çıkar ve analiz et"""
        try:
            # Inline scripts
            inline_scripts = await page.evaluate('''() => {
                const scripts = [];
                document.querySelectorAll('script:not([src])').forEach(s => {
                    if (s.innerText.length > 10) {
                        scripts.push(s.innerText.substring(0, 30000));
                    }
                });
                return scripts;
            }''') or []
            
            collected['scripts']['inline'].extend([
                {'url': 'inline', 'code': code} for code in inline_scripts
            ])
            
            # Library detection from scripts
            all_script_code = ' '.join(s.get('code', '') for s in collected['scripts']['inline'])
            
            # Detect libraries
            libraries = self._detect_libraries(all_script_code)
            collected['scripts']['detected_libraries'] = libraries
            
        except Exception as e:
            collected['errors'].append(f"Script extraction: {str(e)[:100]}")
    
    def _detect_libraries(self, code: str) -> List[str]:
        """JavaScript library'leri tespit et"""
        libraries = []
        
        patterns = {
            'react': [r'react', r'React\.', r'useState', r'useEffect'],
            'vue': [r'Vue\.', r'v-model', r'v-if'],
            'angular': [r'angular', r'ng-', r'@angular'],
            'next.js': [r'next/', r'_next/', r'Next\.js'],
            'nuxt': [r'nuxt', r'Nuxt'],
            'svelte': [r'svelte'],
            'jquery': [r'jQuery', r'\$\('],
            'tailwind': [r'tailwind'],
            'bootstrap': [r'bootstrap'],
            
            # AI SDKs
            'openai-sdk': [r'openai', r'OpenAI', r'createCompletion', r'createChatCompletion'],
            'anthropic-sdk': [r'anthropic', r'Anthropic', r'@anthropic-ai'],
            'langchain': [r'langchain', r'LangChain'],
            'vercel-ai': [r'ai/react', r'@vercel/ai', r'useChat', r'useCompletion'],
            'huggingface': [r'huggingface', r'@huggingface', r'transformers'],
            'replicate': [r'replicate', r'Replicate'],
            
            # Payment SDKs
            'stripe': [r'stripe', r'Stripe\(', r'stripe\.js', r'@stripe'],
            'paypal': [r'paypal', r'PayPal'],
            'paddle': [r'paddle', r'Paddle'],
            'lemonsqueezy': [r'lemonsqueezy', r'lemon_squeezy'],
            'gumroad': [r'gumroad', r'Gumroad'],
            'chargebee': [r'chargebee', r'Chargebee'],
            'recurly': [r'recurly', r'Recurly'],
            
            # Analytics
            'google-analytics': [r'gtag\(', r'google-analytics', r'GoogleAnalytics'],
            'mixpanel': [r'mixpanel', r'Mixpanel'],
            'amplitude': [r'amplitude', r'Amplitude'],
            'segment': [r'segment', r'analytics\.track'],
            'hotjar': [r'hotjar', r'Hotjar'],
            'intercom': [r'intercom', r'Intercom'],
            'crisp': [r'crisp\.chat', r'\$crisp'],
        }
        
        code_lower = code.lower()
        
        for lib, lib_patterns in patterns.items():
            for pattern in lib_patterns:
                if re.search(pattern, code, re.IGNORECASE):
                    if lib not in libraries:
                        libraries.append(lib)
                    break
        
        return libraries
    
    async def _extract_storage(self, page: Page, context, collected: Dict):
        """Storage verilerini çıkar"""
        try:
            # Cookies
            cookies = await context.cookies()
            collected['storage']['cookies'] = [
                {
                    'name': c['name'],
                    'domain': c.get('domain', ''),
                    'value_preview': c['value'][:50] if len(c['value']) < 100 else '[LONG]',
                }
                for c in cookies[:30]
            ]
            
            # LocalStorage
            collected['storage']['local_storage'] = await page.evaluate('''() => {
                const storage = {};
                for (let i = 0; i < localStorage.length && i < 20; i++) {
                    const key = localStorage.key(i);
                    const value = localStorage.getItem(key);
                    storage[key] = value ? value.substring(0, 200) : '';
                }
                return storage;
            }''') or {}
            
            # SessionStorage
            collected['storage']['session_storage'] = await page.evaluate('''() => {
                const storage = {};
                for (let i = 0; i < sessionStorage.length && i < 20; i++) {
                    const key = sessionStorage.key(i);
                    const value = sessionStorage.getItem(key);
                    storage[key] = value ? value.substring(0, 200) : '';
                }
                return storage;
            }''') or {}
            
        except Exception as e:
            collected['errors'].append(f"Storage extraction: {str(e)[:100]}")
    
    async def _extract_forms(self, page: Page, collected: Dict):
        """Form'ları çıkar"""
        try:
            collected['forms'] = await page.evaluate('''() => {
                const forms = [];
                document.querySelectorAll('form').forEach(f => {
                    const inputs = [];
                    f.querySelectorAll('input, select, textarea').forEach(i => {
                        inputs.push({
                            type: i.type || i.tagName.toLowerCase(),
                            name: i.name || '',
                            placeholder: i.placeholder || '',
                        });
                    });
                    forms.push({
                        action: f.action || '',
                        method: f.method || 'get',
                        inputs: inputs.slice(0, 10),
                    });
                });
                return forms.slice(0, 10);
            }''') or []
        except:
            pass
    
    async def _extract_links(self, page: Page, collected: Dict, base_url: str):
        """Link'leri çıkar"""
        try:
            base_domain = urlparse(base_url).netloc.replace('www.', '')
            
            links = await page.evaluate('''() => {
                const links = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    links.push({
                        href: a.href,
                        text: a.innerText.substring(0, 100),
                    });
                });
                return links;
            }''') or []
            
            for link in links[:100]:
                href = link['href']
                text = link['text'].lower()
                
                try:
                    link_domain = urlparse(href).netloc.replace('www.', '')
                    
                    if link_domain == base_domain:
                        collected['links']['internal'].append(href)
                        
                        # Pricing page detection
                        if any(kw in href.lower() or kw in text for kw in 
                               ['pricing', 'price', 'plans', 'subscribe', 'premium', 'upgrade', 'billing']):
                            collected['links']['pricing_pages'].append(href)
                    else:
                        collected['links']['external'].append(href)
                        
                except:
                    pass
                    
        except Exception as e:
            collected['errors'].append(f"Links extraction: {str(e)[:100]}")
    
    async def _find_pricing_page(self, page: Page, collected: Dict, base_url: str) -> Optional[str]:
        """Pricing sayfasını bul"""
        # Önce toplanan linklerden bak
        if collected['links']['pricing_pages']:
            return collected['links']['pricing_pages'][0]
        
        # Common pricing URLs dene
        base = base_url.rstrip('/')
        common_paths = ['/pricing', '/plans', '/price', '/subscribe', '/premium', '/pro', '/upgrade']
        
        for path in common_paths:
            try:
                test_url = base + path
                response = await page.goto(test_url, wait_until='domcontentloaded', timeout=5000)
                if response and response.status == 200:
                    return test_url
            except:
                pass
        
        return None
    
    async def _crawl_pricing_page(self, context, url: str) -> Dict:
        """Pricing sayfasını özel olarak tara"""
        pricing_data = {
            'url': url,
            'plans': [],
            'prices': [],
            'features': [],
            'payment_providers': [],
        }
        
        try:
            page = await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=10000)
            await asyncio.sleep(2)
            
            # Page text
            text = await page.evaluate('''() => {
                return document.body.innerText || '';
            }''') or ''
            
            # Price extraction
            price_patterns = [
                r'\$(\d+(?:\.\d{2})?)',
                r'(\d+(?:\.\d{2})?)\s*(?:USD|EUR|GBP)',
                r'(\d+)\s*(?:/mo|/month|per month)',
                r'free(?:\s+tier|\s+plan)?',
            ]
            
            for pattern in price_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                pricing_data['prices'].extend(matches[:10])
            
            # Plan names
            plan_patterns = [
                r'(free|basic|starter|pro|professional|premium|enterprise|business|team|individual)\s*(?:plan|tier)?',
            ]
            
            for pattern in plan_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                pricing_data['plans'].extend(list(set(matches))[:10])
            
            await page.close()
            
        except Exception as e:
            pricing_data['error'] = str(e)[:100]
        
        return pricing_data