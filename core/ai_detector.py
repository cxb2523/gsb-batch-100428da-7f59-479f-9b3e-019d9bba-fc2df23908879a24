# core/ai_detector.py
"""
🔥 AI DETECTOR - Hangi AI teknolojisi kullanılıyor?
Network, Script, Content analizi ile tespit
"""

import re
from typing import Dict, List, Set, Tuple
from dataclasses import dataclass


@dataclass
class AIEvidence:
    """AI tespit kanıtı"""
    source: str  # 'network', 'script', 'content', 'api_response'
    type: str    # 'api_call', 'sdk', 'model_name', 'claim'
    provider: str
    model: str
    confidence: float  # 0.0 - 1.0
    raw_evidence: str


class AIDetector:
    """
    AI teknolojisi tespit motoru.
    Çoklu kaynaklardan analiz yapar:
    - Network traffic (API calls)
    - JavaScript (SDK usage)
    - Content (Model mentions)
    - API responses (Model info)
    """
    
    # API Endpoint Patterns - Yüksek güvenilirlik
    API_PATTERNS = {
        'openai': {
            'endpoints': [
                r'api\.openai\.com',
                r'chat\.openai\.com/backend-api',
                r'chatgpt\.com',
            ],
            'models': ['gpt-4', 'gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo', 'dall-e-3', 'dall-e-2', 'whisper', 'tts'],
        },
        'anthropic': {
            'endpoints': [
                r'api\.anthropic\.com',
                r'claude\.ai',
            ],
            'models': ['claude-3-opus', 'claude-3-sonnet', 'claude-3-haiku', 'claude-2', 'claude-instant'],
        },
        'google': {
            'endpoints': [
                r'generativelanguage\.googleapis\.com',
                r'aiplatform\.googleapis\.com',
                r'bard\.google\.com',
                r'gemini\.google\.com',
            ],
            'models': ['gemini-pro', 'gemini-ultra', 'gemini-1.5-pro', 'gemini-1.5-flash', 'palm-2', 'bard'],
        },
        'mistral': {
            'endpoints': [r'api\.mistral\.ai'],
            'models': ['mistral-large', 'mistral-medium', 'mistral-small', 'mixtral-8x7b', 'mistral-7b'],
        },
        'cohere': {
            'endpoints': [r'api\.cohere\.ai', r'cohere\.com'],
            'models': ['command-r', 'command-r-plus', 'command', 'embed'],
        },
        'replicate': {
            'endpoints': [r'api\.replicate\.com', r'replicate\.delivery'],
            'models': ['stable-diffusion', 'sdxl', 'llama', 'flux'],
        },
        'huggingface': {
            'endpoints': [r'api-inference\.huggingface\.co', r'huggingface\.co/api'],
            'models': ['transformers', 'diffusers'],
        },
        'together': {
            'endpoints': [r'api\.together\.xyz', r'together\.ai'],
            'models': ['llama-3', 'mixtral', 'qwen'],
        },
        'groq': {
            'endpoints': [r'api\.groq\.com'],
            'models': ['llama', 'mixtral', 'gemma'],
        },
        'perplexity': {
            'endpoints': [r'api\.perplexity\.ai'],
            'models': ['pplx-online', 'sonar'],
        },
        'stability': {
            'endpoints': [r'api\.stability\.ai', r'stabilityai'],
            'models': ['stable-diffusion-3', 'stable-diffusion-xl', 'sdxl', 'sd3'],
        },
        'midjourney': {
            'endpoints': [r'midjourney\.com', r'mj\.run'],
            'models': ['midjourney-v6', 'midjourney-v5'],
        },
        'elevenlabs': {
            'endpoints': [r'api\.elevenlabs\.io', r'elevenlabs\.io'],
            'models': ['eleven-monolingual', 'eleven-multilingual', 'eleven-turbo'],
        },
        'runway': {
            'endpoints': [r'api\.runwayml\.com', r'runwayml\.com'],
            'models': ['gen-2', 'gen-3'],
        },
        'deepl': {
            'endpoints': [r'api\.deepl\.com', r'deepl\.com'],
            'models': ['deepl-translate'],
        },
        'aws-bedrock': {
            'endpoints': [r'bedrock.*amazonaws\.com', r'bedrock-runtime'],
            'models': ['titan', 'claude', 'llama', 'stable-diffusion'],
        },
        'azure-openai': {
            'endpoints': [r'openai\.azure\.com', r'cognitiveservices.*openai'],
            'models': ['gpt-4', 'gpt-35-turbo', 'dall-e'],
        },
    }
    
    # Model Name Patterns - İçerikte arama
    MODEL_PATTERNS = [
        # OpenAI
        (r'gpt-?4[o\-]?(?:turbo|vision|mini)?', 'openai', 'gpt-4'),
        (r'gpt-?3\.?5[- ]?turbo', 'openai', 'gpt-3.5-turbo'),
        (r'chatgpt(?:-?4)?', 'openai', 'chatgpt'),
        (r'dall[- ]?e[- ]?[23]?', 'openai', 'dall-e'),
        (r'whisper(?:-?[a-z]+)?', 'openai', 'whisper'),
        (r'codex', 'openai', 'codex'),
        
        # Anthropic
        (r'claude[- ]?3[- ]?(?:opus|sonnet|haiku)', 'anthropic', 'claude-3'),
        (r'claude[- ]?2(?:\.[01])?', 'anthropic', 'claude-2'),
        (r'claude[- ]?instant', 'anthropic', 'claude-instant'),
        
        # Google
        (r'gemini[- ]?(?:pro|ultra|flash|nano|1\.5|2\.0)', 'google', 'gemini'),
        (r'bard', 'google', 'bard'),
        (r'palm[- ]?2?', 'google', 'palm'),
        
        # Meta
        (r'llama[- ]?[23][- ]?(?:\d+b)?', 'meta', 'llama'),
        (r'code[- ]?llama', 'meta', 'codellama'),
        
        # Mistral
        (r'mixtral[- ]?(?:\d+x\d+)?', 'mistral', 'mixtral'),
        (r'mistral[- ]?(?:large|medium|small|\d+b)?', 'mistral', 'mistral'),
        
        # Image
        (r'stable[- ]?diffusion[- ]?(?:xl|3|2\.?1?)?', 'stability', 'stable-diffusion'),
        (r'sdxl', 'stability', 'sdxl'),
        (r'midjourney(?:[- ]?v\d)?', 'midjourney', 'midjourney'),
        (r'flux(?:[- ]?\d)?', 'black-forest', 'flux'),
        
        # Voice
        (r'elevenlabs?', 'elevenlabs', 'elevenlabs'),
        (r'eleven[- ]?labs?', 'elevenlabs', 'elevenlabs'),
        
        # Other
        (r'cohere[- ]?(?:command|embed)', 'cohere', 'cohere'),
        (r'perplexity', 'perplexity', 'perplexity'),
    ]
    
    # SDK Patterns - JavaScript'te arama
    SDK_PATTERNS = {
        'openai': [
            r'from\s+[\'"]openai[\'"]',
            r'import\s+.*openai',
            r'new\s+OpenAI\s*\(',
            r'openai\.chat\.completions',
            r'createChatCompletion',
            r'openai/v\d+',
        ],
        'anthropic': [
            r'@anthropic-ai/sdk',
            r'from\s+[\'"]anthropic[\'"]',
            r'new\s+Anthropic\s*\(',
            r'anthropic\.messages',
        ],
        'langchain': [
            r'langchain',
            r'@langchain/',
            r'from\s+langchain',
            r'LangChain',
        ],
        'vercel-ai': [
            r'@vercel/ai',
            r'ai/react',
            r'useChat',
            r'useCompletion',
            r'streamText',
        ],
        'huggingface': [
            r'@huggingface/',
            r'transformers\.js',
            r'@xenova/transformers',
            r'HfInference',
        ],
        'replicate': [
            r'replicate',
            r'Replicate\s*\(',
        ],
        'google-ai': [
            r'@google/generative-ai',
            r'GoogleGenerativeAI',
            r'gemini-pro',
        ],
        'cohere': [
            r'cohere-ai',
            r'CohereClient',
        ],
    }
    
    def detect(self, crawl_data: Dict) -> Dict:
        """
        Tüm kaynaklardan AI tespiti yap
        """
        evidences: List[AIEvidence] = []
        
        # 1. Network Traffic Analysis
        evidences.extend(self._analyze_network(crawl_data))
        
        # 2. Script Analysis
        evidences.extend(self._analyze_scripts(crawl_data))
        
        # 3. Content Analysis
        evidences.extend(self._analyze_content(crawl_data))
        
        # 4. API Response Analysis
        evidences.extend(self._analyze_api_responses(crawl_data))
        
        # 5. Storage Analysis
        evidences.extend(self._analyze_storage(crawl_data))
        
        # Sonuçları derle
        return self._compile_results(evidences)
    
    def _analyze_network(self, data: Dict) -> List[AIEvidence]:
        """Network traffic'ten AI tespiti"""
        evidences = []
        requests = data.get('network', {}).get('requests', [])
        
        for req in requests:
            url = req.get('url', '').lower()
            
            for provider, config in self.API_PATTERNS.items():
                for pattern in config['endpoints']:
                    if re.search(pattern, url, re.IGNORECASE):
                        evidences.append(AIEvidence(
                            source='network',
                            type='api_call',
                            provider=provider,
                            model=self._guess_model_from_url(url, provider),
                            confidence=0.95,
                            raw_evidence=url[:200]
                        ))
                        break
        
        return evidences
    
    def _analyze_scripts(self, data: Dict) -> List[AIEvidence]:
        """JavaScript'ten SDK tespiti"""
        evidences = []
        
        scripts = data.get('scripts', {}).get('inline', [])
        all_code = ' '.join(s.get('code', '') for s in scripts)
        
        # SDK patterns
        for provider, patterns in self.SDK_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, all_code, re.IGNORECASE):
                    evidences.append(AIEvidence(
                        source='script',
                        type='sdk',
                        provider=provider,
                        model='',
                        confidence=0.85,
                        raw_evidence=f"SDK pattern: {pattern}"
                    ))
                    break
        
        # Model names in scripts
        for pattern, provider, model in self.MODEL_PATTERNS:
            matches = re.findall(pattern, all_code, re.IGNORECASE)
            if matches:
                evidences.append(AIEvidence(
                    source='script',
                    type='model_name',
                    provider=provider,
                    model=model,
                    confidence=0.75,
                    raw_evidence=f"Model: {matches[0]}"
                ))
        
        return evidences
    
    def _analyze_content(self, data: Dict) -> List[AIEvidence]:
        """Sayfa içeriğinden AI tespiti"""
        evidences = []
        
        content = data.get('content', {})
        text = f"{content.get('title', '')} {content.get('meta_description', '')} {content.get('page_text', '')}".lower()
        
        # Model patterns
        for pattern, provider, model in self.MODEL_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                # Count occurrences for confidence
                count = len(matches)
                confidence = min(0.7, 0.3 + (count * 0.1))
                
                evidences.append(AIEvidence(
                    source='content',
                    type='model_mention',
                    provider=provider,
                    model=model,
                    confidence=confidence,
                    raw_evidence=f"Mentioned {count}x: {matches[0]}"
                ))
        
        # Marketing claims (lower confidence)
        claim_patterns = [
            (r'powered\s+by\s+(gpt|claude|gemini|llama|ai)', 0.5),
            (r'using\s+(gpt|claude|gemini|openai|anthropic)', 0.6),
            (r'built\s+(?:on|with)\s+(gpt|claude|openai)', 0.6),
            (r'(gpt|claude|gemini)\s+integration', 0.5),
        ]
        
        for pattern, conf in claim_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                evidences.append(AIEvidence(
                    source='content',
                    type='claim',
                    provider=self._extract_provider(match.group(1)),
                    model='',
                    confidence=conf,
                    raw_evidence=match.group(0)[:100]
                ))
        
        return evidences
    
    def _analyze_api_responses(self, data: Dict) -> List[AIEvidence]:
        """API response'lardan AI tespiti"""
        evidences = []
        
        api_calls = data.get('network', {}).get('api_calls', [])
        
        for call in api_calls:
            body = call.get('body_preview', '')
            
            # Model field in JSON
            model_match = re.search(r'"model"\s*:\s*"([^"]+)"', body)
            if model_match:
                model_name = model_match.group(1)
                provider = self._identify_provider(model_name)
                
                evidences.append(AIEvidence(
                    source='api_response',
                    type='model_field',
                    provider=provider,
                    model=model_name,
                    confidence=0.90,
                    raw_evidence=f"Model: {model_name}"
                ))
        
        return evidences
    
    def _analyze_storage(self, data: Dict) -> List[AIEvidence]:
        """Storage'dan AI tespiti"""
        evidences = []
        
        storage = data.get('storage', {})
        
        # Check localStorage/sessionStorage keys
        for key, value in storage.get('local_storage', {}).items():
            key_lower = key.lower()
            if any(kw in key_lower for kw in ['openai', 'anthropic', 'api_key', 'model', 'gpt', 'claude']):
                evidences.append(AIEvidence(
                    source='storage',
                    type='storage_key',
                    provider=self._extract_provider(key),
                    model='',
                    confidence=0.70,
                    raw_evidence=f"Key: {key}"
                ))
        
        # Check cookies
        for cookie in storage.get('cookies', []):
            name = cookie.get('name', '').lower()
            if any(kw in name for kw in ['openai', 'anthropic', 'ai_session']):
                evidences.append(AIEvidence(
                    source='storage',
                    type='cookie',
                    provider=self._extract_provider(name),
                    model='',
                    confidence=0.60,
                    raw_evidence=f"Cookie: {name}"
                ))
        
        return evidences
    
    def _guess_model_from_url(self, url: str, provider: str) -> str:
        """URL'den model tahmini"""
        models = self.API_PATTERNS.get(provider, {}).get('models', [])
        for model in models:
            if model in url.lower():
                return model
        return ''
    
    def _extract_provider(self, text: str) -> str:
        """Metinden provider çıkar"""
        text = text.lower()
        providers = {
            'openai': ['openai', 'gpt', 'chatgpt', 'dall-e'],
            'anthropic': ['anthropic', 'claude'],
            'google': ['google', 'gemini', 'bard', 'palm'],
            'meta': ['meta', 'llama'],
            'mistral': ['mistral', 'mixtral'],
            'stability': ['stability', 'stable-diffusion', 'sdxl'],
        }
        
        for provider, keywords in providers.items():
            if any(kw in text for kw in keywords):
                return provider
        return 'unknown'
    
    def _identify_provider(self, model_name: str) -> str:
        """Model isminden provider belirle"""
        model = model_name.lower()
        
        if any(m in model for m in ['gpt', 'davinci', 'dall-e', 'whisper']):
            return 'openai'
        elif 'claude' in model:
            return 'anthropic'
        elif any(m in model for m in ['gemini', 'palm', 'bard']):
            return 'google'
        elif any(m in model for m in ['llama', 'codellama']):
            return 'meta'
        elif any(m in model for m in ['mistral', 'mixtral']):
            return 'mistral'
        elif any(m in model for m in ['stable-diffusion', 'sdxl']):
            return 'stability'
        
        return 'unknown'
    
    def _compile_results(self, evidences: List[AIEvidence]) -> Dict:
        """Sonuçları derle"""
        
        # Provider'lara göre grupla
        by_provider: Dict[str, List[AIEvidence]] = {}
        for ev in evidences:
            if ev.provider not in by_provider:
                by_provider[ev.provider] = []
            by_provider[ev.provider].append(ev)
        
        # Her provider için özet
        providers = []
        models = []
        total_confidence = 0
        
        for provider, provider_evidences in by_provider.items():
            if provider == 'unknown':
                continue
            
            # En yüksek confidence'lı evidence
            max_conf = max(ev.confidence for ev in provider_evidences)
            total_confidence = max(total_confidence, max_conf)
            
            # Models
            provider_models = set()
            for ev in provider_evidences:
                if ev.model:
                    provider_models.add(ev.model)
                    models.append({
                        'model': ev.model,
                        'provider': provider,
                        'confidence': ev.confidence,
                        'source': ev.source,
                    })
            
            providers.append({
                'name': provider,
                'confidence': max_conf,
                'models': list(provider_models),
                'evidence_count': len(provider_evidences),
                'sources': list(set(ev.source for ev in provider_evidences)),
            })
        
        # Evidence logs
        evidence_logs = []
        for ev in sorted(evidences, key=lambda x: x.confidence, reverse=True)[:10]:
            evidence_logs.append(
                f"> [{ev.source.upper()}] {ev.provider}: {ev.type} - {ev.raw_evidence[:80]}"
            )
        
        return {
            'providers': sorted(providers, key=lambda x: x['confidence'], reverse=True),
            'models': models,
            'total_confidence': round(total_confidence * 100),
            'evidence_count': len(evidences),
            'evidence_logs': evidence_logs,
            'has_ai': total_confidence > 0.3,
        }