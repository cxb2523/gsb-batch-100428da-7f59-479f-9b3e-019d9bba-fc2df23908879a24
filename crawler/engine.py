# crawler/engine.py
"""
🔥 IMPERIAL CRAWLER - Enhanced with Deep Interceptor
"""

import asyncio
from playwright.async_api import async_playwright, Browser, Page
from urllib.parse import urlparse
from typing import Dict, Optional


class ImperialCrawler:
    def __init__(self, timeout: int = 20000):  # 20 saniyeye düşür
        self.timeout = timeout
        self.browser: Optional[Browser] = None
        self.playwright = None
        
    async def start(self):
        print("🚀 Starting Crawler...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-gpu',
                '--disable-extensions',
                '--disable-plugins',
                '--disable-images',  # Hızlandırmak için resimleri kapat
            ]
        )
        print("✅ Browser ready")
    
    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("🛑 Crawler stopped")
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, *args):
        await self.stop()
    
    async def analyze(self, url: str) -> Dict:
        """Analyze a URL with enhanced interception"""
        
        domain = urlparse(url).netloc.replace('www.', '')
        print(f"🔎 Analyzing: {url[:60]}")
        
        context = None
        page = None
        
        try:
            # Yeni context
            context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 720},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                java_script_enabled=True,
            )
            
            # Block unnecessary resources for speed
            await context.route("**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf}", 
                              lambda route: route.abort())
            
            page = await context.new_page()
            
            # ========== NETWORK INTERCEPTION ==========
            network_data = {
                'ai_calls': [],
                'detected_models': [],
                'detected_libraries': [],
                'providers': set(),
            }
            
            # API patterns
            AI_API_HOSTS = {
                'openai': ['api.openai.com', 'chat.openai.com'],
                'anthropic': ['api.anthropic.com', 'claude.ai'],
                'google': ['generativelanguage.googleapis.com', 'bard.google.com'],
                'replicate': ['api.replicate.com'],
                'together': ['api.together.xyz'],
                'groq': ['api.groq.com'],
                'huggingface': ['api-inference.huggingface.co'],
                'cohere': ['api.cohere.ai'],
                'mistral': ['api.mistral.ai'],
                'perplexity': ['api.perplexity.ai'],
            }
            
            def handle_request(request):
                url_lower = request.url.lower()
                for provider, hosts in AI_API_HOSTS.items():
                    for host in hosts:
                        if host in url_lower:
                            network_data['ai_calls'].append({
                                'provider': provider,
                                'url': request.url[:100],
                                'method': request.method
                            })
                            network_data['providers'].add(provider)
                            print(f"   🎯 API: {provider}")
            
            page.on("request", handle_request)
            
            # ========== NAVIGATE ==========
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=self.timeout)
                # Kısa bir bekleme - dinamik içerik için
                await asyncio.sleep(2)
            except Exception as nav_error:
                # Timeout olsa bile devam et, belki bir şeyler yüklendi
                if 'Timeout' not in str(type(nav_error).__name__):
                    raise nav_error
            
            # ========== EXTRACT DATA ==========
            title = await page.title() or ""
            
            # Page text
            page_text = ""
            try:
                page_text = await page.evaluate('''() => {
                    // Remove script and style content
                    const scripts = document.querySelectorAll('script, style, noscript');
                    scripts.forEach(s => s.remove());
                    return document.body ? document.body.innerText : '';
                }''')
            except:
                pass
            
            # Meta description
            meta_desc = ""
            try:
                meta_desc = await page.evaluate('''() => {
                    const meta = document.querySelector('meta[name="description"]');
                    return meta ? meta.getAttribute('content') : '';
                }''')
            except:
                pass
            
            # Inline scripts for model detection
            inline_scripts = ""
            try:
                inline_scripts = await page.evaluate('''() => {
                    const scripts = document.querySelectorAll('script:not([src])');
                    return Array.from(scripts).map(s => s.innerText).join(' ').substring(0, 50000);
                }''')
            except:
                pass
            
            print(f"✅ Done: {title[:50]}")
            
            # ========== MODEL DETECTION FROM CONTENT ==========
            import re
            
            all_text = f"{title} {meta_desc} {page_text} {inline_scripts}".lower()
            
            MODEL_PATTERNS = [
                (r'gpt-?4o?[-\w]*', 'openai'),
                (r'gpt-?3\.?5[-\w]*', 'openai'),
                (r'dall[-\s]?e[-\s]?[23]?', 'openai'),
                (r'claude[-\s]?3[-\s]?(opus|sonnet|haiku)', 'anthropic'),
                (r'claude[-\s]?2[\.\d]*', 'anthropic'),
                (r'gemini[-\s]?(pro|ultra|flash|nano|1\.5|2)', 'google'),
                (r'llama[-\s]?[23][-\s]?\d+b?', 'meta'),
                (r'mixtral[-\s]?\d+x\d+', 'mistral'),
                (r'mistral[-\s]?\d+b?', 'mistral'),
                (r'stable[-\s]?diffusion[-\s]?(xl|3|2)?', 'stability'),
                (r'sdxl', 'stability'),
                (r'midjourney', 'midjourney'),
                (r'whisper', 'openai'),
                (r'codex', 'openai'),
            ]
            
            for pattern, provider in MODEL_PATTERNS:
                matches = re.findall(pattern, all_text, re.IGNORECASE)
                if matches:
                    for match in matches[:3]:  # Max 3 per pattern
                        model_name = match if isinstance(match, str) else match[0] if match else pattern
                        if model_name and len(model_name) > 2:
                            network_data['detected_models'].append(model_name)
                            network_data['providers'].add(provider)
            
            # Deduplicate models
            network_data['detected_models'] = list(set(network_data['detected_models']))
            network_data['providers'] = list(network_data['providers'])
            
            if network_data['detected_models']:
                print(f"   🔍 Models: {network_data['detected_models'][:3]}")
            
            result = {
                'url': url,
                'domain': domain,
                'title': title,
                'meta_description': meta_desc,
                'page_text': page_text[:20000],
                'inline_scripts': inline_scripts[:10000],
                'network': network_data,
            }
            
            return result
            
        except Exception as e:
            print(f"❌ Failed: {str(e)[:40]}")
            raise
            
        finally:
            if context:
                await context.close()