"""Network traffic interceptor - AI API detection"""

import re
from typing import Dict, List
from playwright.async_api import Page, Request, Response


class NetworkInterceptor:
    """Captures network traffic to detect AI API calls"""
    
    AI_PATTERNS = {
        'openai': [r'api\.openai\.com', r'chat\.openai\.com'],
        'anthropic': [r'api\.anthropic\.com', r'claude\.ai'],
        'google': [r'generativelanguage\.googleapis\.com', r'aiplatform\.googleapis\.com'],
        'replicate': [r'api\.replicate\.com'],
        'together': [r'api\.together\.xyz'],
        'groq': [r'api\.groq\.com'],
        'huggingface': [r'api-inference\.huggingface\.co'],
        'cohere': [r'api\.cohere\.ai'],
        'mistral': [r'api\.mistral\.ai'],
        'perplexity': [r'api\.perplexity\.ai'],
    }
    
    MODEL_PATTERNS = [
        r'gpt-4[o\-turbo]*',
        r'gpt-3\.5-turbo',
        r'claude-3[.\-\w]*',
        r'claude-2',
        r'gemini-[\w]+',
        r'llama-[\d]+',
        r'mixtral',
        r'mistral',
        r'stable-diffusion',
        r'dall-e',
    ]
    
    def __init__(self):
        self.requests: List[Dict] = []
        self.ai_calls: List[Dict] = []
        self.detected_models: List[str] = []
        
    async def attach(self, page: Page):
        page.on("request", self._handle_request)
        page.on("response", self._handle_response)
        
    def _handle_request(self, request: Request):
        url = request.url
        self.requests.append({'url': url, 'method': request.method})
        
        for provider, patterns in self.AI_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    self.ai_calls.append({
                        'provider': provider,
                        'url': url,
                        'method': request.method,
                    })
                    print(f"🎯 AI API: {provider} → {url[:60]}")
                    
                    if request.post_data:
                        self._extract_models(request.post_data)
    
    async def _handle_response(self, response: Response):
        try:
            if response.status == 200:
                content_type = response.headers.get('content-type', '')
                if 'json' in content_type:
                    body = await response.text()
                    self._extract_models(body)
        except:
            pass
            
    def _extract_models(self, text: str):
        for pattern in self.MODEL_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if match not in self.detected_models:
                    self.detected_models.append(match)
                    print(f"🔍 Model: {match}")
    
    def get_results(self) -> Dict:
        return {
            'total_requests': len(self.requests),
            'ai_calls': self.ai_calls,
            'detected_models': self.detected_models,
            'providers_found': list(set(c['provider'] for c in self.ai_calls)),
        }
    
    def clear(self):
        self.requests = []
        self.ai_calls = []
        self.detected_models = []