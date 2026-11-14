# crawler/enhanced_interceptor.py
"""
🔥 ENHANCED NETWORK INTERCEPTOR
Deeper analysis of network traffic, JavaScript, and DOM
"""

import re
import json
from typing import Dict, List, Set
from playwright.async_api import Page, Request, Response


class EnhancedInterceptor:
    """
    Advanced network and content analysis:
    1. Network traffic (XHR/Fetch)
    2. JavaScript content
    3. DOM analysis
    4. Headers analysis
    5. WebSocket monitoring
    """
    
    # Extended API patterns
    AI_API_PATTERNS = {
        'openai': [
            r'api\.openai\.com',
            r'chat\.openai\.com',
            r'platform\.openai\.com',
        ],
        'anthropic': [
            r'api\.anthropic\.com',
            r'claude\.ai',
        ],
        'google': [
            r'generativelanguage\.googleapis\.com',
            r'aiplatform\.googleapis\.com',
            r'bard\.google\.com',
            r'makersuite\.google\.com',
        ],
        'replicate': [r'api\.replicate\.com', r'replicate\.com/api'],
        'together': [r'api\.together\.xyz', r'together\.ai'],
        'groq': [r'api\.groq\.com'],
        'huggingface': [r'api-inference\.huggingface\.co', r'huggingface\.co/api'],
        'cohere': [r'api\.cohere\.ai', r'cohere\.ai/api'],
        'mistral': [r'api\.mistral\.ai'],
        'perplexity': [r'api\.perplexity\.ai'],
        'stability': [r'api\.stability\.ai', r'stablediffusionapi\.com'],
        'runpod': [r'api\.runpod\.io'],
        'modal': [r'modal\.com/api'],
        'fireworks': [r'api\.fireworks\.ai'],
        'anyscale': [r'api\.anyscale\.com'],
    }
    
    # Model signatures in different formats
    MODEL_PATTERNS = [
        # OpenAI
        r'gpt-4[o\-\w]*',
        r'gpt-3\.5-turbo[\w-]*',
        r'dall-e-[23]',
        r'text-davinci-[\w-]+',
        r'whisper-[\w-]+',
        
        # Anthropic
        r'claude-3[-\s]*(opus|sonnet|haiku)',
        r'claude-2\.[\d]',
        r'claude-instant-[\w-]+',
        
        # Google
        r'gemini-[\w-]+',
        r'palm-[\w-]+',
        r'bard',
        
        # Meta
        r'llama-?[23]-\d+b',
        r'codellama-\d+b',
        
        # Mistral
        r'mixtral-\d+x\d+b',
        r'mistral-\d+b',
        r'mistral-[\w-]+',
        
        # Open Source
        r'stable-diffusion[\w-]*',
        r'sdxl',
        r'vicuna-\d+b',
        r'alpaca-\d+b',
        r'falcon-\d+b',
        
        # Other
        r'command-[\w-]+',  # Cohere
        r'j2-[\w-]+',  # AI21
    ]
    
    # JavaScript library signatures
    JS_LIBRARY_PATTERNS = {
        'openai': [
            r'openai[/-]',
            r'@openai/',
            r'from ["\']openai["\']',
        ],
        'anthropic': [
            r'@anthropic-ai/sdk',
            r'anthropic[/-]',
        ],
        'langchain': [
            r'langchain',
            r'@langchain/',
        ],
        'llamaindex': [
            r'llamaindex',
            r'llama-index',
        ],
        'vercel-ai': [
            r'ai/react',
            r'@vercel/ai',
            r'vercel.*sdk',
        ],
        'replicate': [
            r'replicate',
        ],
        'transformers': [
            r'@xenova/transformers',
            r'transformers\.js',
        ],
    }
    
    def __init__(self):
        self.requests: List[Dict] = []
        self.responses: List[Dict] = []
        self.ai_calls: List[Dict] = []
        self.detected_models: Set[str] = set()
        self.detected_libraries: Set[str] = set()
        self.js_content: List[str] = []
        self.suspicious_headers: List[Dict] = []
        
    async def attach(self, page: Page):
        """Attach all event listeners"""
        
        # Network events
        page.on("request", self._handle_request)
        page.on("response", self._handle_response)
        page.on("requestfinished", self._handle_request_finished)
        
        # Console logs (some sites log API info)
        page.on("console", self._handle_console)
        
        # Page errors (might reveal API issues)
        page.on("pageerror", self._handle_error)
    
    def _handle_request(self, request: Request):
        """Deep request analysis"""
        
        url = request.url
        method = request.method
        headers = request.headers
        
        # Store request
        req_data = {
            'url': url,
            'method': method,
            'headers': headers,
            'resource_type': request.resource_type
        }
        self.requests.append(req_data)
        
        # Check for AI API calls
        for provider, patterns in self.AI_API_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    self.ai_calls.append({
                        'provider': provider,
                        'url': url,
                        'method': method,
                        'type': 'api_call'
                    })
                    print(f"🎯 {provider.upper()} API: {method} {url[:60]}")
                    
                    # Check POST data for models
                    post_data = request.post_data
                    if post_data:
                        self._extract_from_text(post_data)
        
        # Check headers for clues
        self._analyze_headers(headers, url)
        
        # Check if JavaScript
        if request.resource_type == 'script':
            # Will be analyzed in response
            pass
    
    async def _handle_response(self, response: Response):
        """Deep response analysis"""
        
        url = response.url
        status = response.status
        headers = response.headers
        
        try:
            # Store response metadata
            resp_data = {
                'url': url,
                'status': status,
                'headers': headers,
            }
            self.responses.append(resp_data)
            
            # Only process successful responses
            if status != 200:
                return
            
            content_type = headers.get('content-type', '')
            
            # JSON responses (likely API responses)
            if 'json' in content_type:
                try:
                    body = await response.text()
                    self._extract_from_text(body)
                    
                    # Try to parse JSON
                    data = json.loads(body)
                    self._analyze_json_response(data, url)
                except:
                    pass
            
            # JavaScript files
            elif 'javascript' in content_type or url.endswith('.js'):
                try:
                    js_code = await response.text()
                    if len(js_code) < 1_000_000:  # Max 1MB
                        self.js_content.append(js_code)
                        self._analyze_javascript(js_code)
                except:
                    pass
            
            # HTML (main page)
            elif 'html' in content_type:
                try:
                    html = await response.text()
                    self._extract_from_text(html)
                except:
                    pass
                    
        except Exception as e:
            pass
    
    def _handle_request_finished(self, request: Request):
        """Final request analysis"""
        pass
    
    def _handle_console(self, msg):
        """Analyze console logs"""
        text = msg.text.lower()
        
        # Check for AI-related logs
        if any(kw in text for kw in ['gpt', 'claude', 'openai', 'anthropic', 'api', 'model']):
            self._extract_from_text(msg.text)
    
    def _handle_error(self, error):
        """Analyze page errors"""
        error_text = str(error).lower()
        
        # API errors might reveal provider
        if 'api' in error_text or 'unauthorized' in error_text:
            self._extract_from_text(str(error))
    
    # ============================================================
    # ANALYSIS METHODS
    # ============================================================
    
    def _analyze_headers(self, headers: Dict, url: str):
        """Analyze request/response headers"""
        
        suspicious = []
        
        # Check for AI-related headers
        for key, value in headers.items():
            key_lower = key.lower()
            value_lower = str(value).lower()
            
            # Authorization headers with AI providers
            if key_lower in ['authorization', 'x-api-key', 'api-key']:
                if any(p in value_lower for p in ['bearer', 'sk-', 'api']):
                    suspicious.append({
                        'header': key,
                        'url': url[:50],
                        'type': 'api_key'
                    })
            
            # Custom AI headers
            if any(kw in key_lower for kw in ['openai', 'anthropic', 'model', 'ai']):
                suspicious.append({
                    'header': f"{key}: {value}",
                    'url': url[:50],
                    'type': 'ai_header'
                })
        
        if suspicious:
            self.suspicious_headers.extend(suspicious)
    
    def _analyze_json_response(self, data: Dict, url: str):
        """Analyze JSON API responses"""
        
        # Look for model information in response
        def search_dict(obj, depth=0):
            if depth > 5:  # Prevent infinite recursion
                return
            
            if isinstance(obj, dict):
                for key, value in obj.items():
                    key_lower = str(key).lower()
                    
                    # Model field
                    if key_lower in ['model', 'model_name', 'engine', 'model_id']:
                        self._extract_from_text(str(value))
                    
                    # Recurse
                    search_dict(value, depth + 1)
                    
            elif isinstance(obj, list):
                for item in obj[:10]:  # Limit array size
                    search_dict(item, depth + 1)
        
        search_dict(data)
    
    def _analyze_javascript(self, js_code: str):
        """Deep JavaScript analysis"""
        
        # 1. Check for library imports
        for lib, patterns in self.JS_LIBRARY_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, js_code):
                    self.detected_libraries.add(lib)
        
        # 2. Look for API keys (obfuscated or not)
        api_key_patterns = [
            r'sk-[A-Za-z0-9]{48}',  # OpenAI
            r'["\']apiKey["\']\s*:\s*["\'][^"\']+["\']',
            r'ANTHROPIC_API_KEY',
            r'OPENAI_API_KEY',
        ]
        
        for pattern in api_key_patterns:
            if re.search(pattern, js_code):
                # Found API key usage (don't log the actual key)
                self.ai_calls.append({
                    'provider': 'detected_api_key',
                    'type': 'js_hardcoded_key',
                    'file': 'javascript'
                })
        
        # 3. Look for model references
        self._extract_from_text(js_code)
    
    def _extract_from_text(self, text: str):
        """Extract model names from any text"""
        
        if not text:
            return
        
        # Find model patterns
        for pattern in self.MODEL_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                # Clean and normalize
                model = match.lower().strip()
                
                # Filter out false positives
                if len(model) >= 3 and model not in self.detected_models:
                    self.detected_models.add(model)
                    print(f"🔍 Model: {model}")
    
    # ============================================================
    # RESULTS
    # ============================================================
    
    def get_results(self) -> Dict:
        """Get comprehensive analysis results"""
        
        providers = list(set(c['provider'] for c in self.ai_calls))
        
        return {
            # Network
            'total_requests': len(self.requests),
            'ai_calls': self.ai_calls,
            'suspicious_headers': self.suspicious_headers,
            
            # Detection
            'providers_found': providers,
            'detected_models': sorted(list(self.detected_models)),
            'detected_libraries': sorted(list(self.detected_libraries)),
            
            # Counts
            'api_call_count': len(self.ai_calls),
            'js_files_analyzed': len(self.js_content),
            
            # Confidence
            'confidence': self._calculate_confidence(),
        }
    
    def _calculate_confidence(self) -> float:
        """Calculate detection confidence"""
        
        confidence = 0.0
        
        # Strong signals
        if self.ai_calls:
            confidence += 0.4
        
        if self.detected_models:
            confidence += 0.3
        
        if self.detected_libraries:
            confidence += 0.2
        
        if self.suspicious_headers:
            confidence += 0.1
        
        return min(1.0, confidence)
    
    def clear(self):
        """Reset for next crawl"""
        self.requests = []
        self.responses = []
        self.ai_calls = []
        self.detected_models = set()
        self.detected_libraries = set()
        self.js_content = []
        self.suspicious_headers = []


# ============================================================
# USAGE IN CRAWLER
# ============================================================

async def test_enhanced_interceptor():
    """Test the enhanced interceptor"""
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        interceptor = EnhancedInterceptor()
        await interceptor.attach(page)
        
        # Test on a site
        await page.goto('https://chat.openai.com', wait_until='networkidle')
        
        results = interceptor.get_results()
        
        print("\n" + "=" * 60)
        print("ENHANCED INTERCEPTOR RESULTS")
        print("=" * 60)
        print(f"\nProviders: {results['providers_found']}")
        print(f"Models: {results['detected_models']}")
        print(f"Libraries: {results['detected_libraries']}")
        print(f"Confidence: {results['confidence']:.0%}")
        print(f"\nAPI Calls: {results['api_call_count']}")
        print(f"JS Files: {results['js_files_analyzed']}")
        
        await browser.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_enhanced_interceptor())
