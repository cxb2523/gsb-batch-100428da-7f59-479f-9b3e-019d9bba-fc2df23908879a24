# analyzer/deep_analyzer.py
"""
🔥 DEEP ANALYZER - Maximum İstihbarat Toplama
AI API'ye minimum bağımlılık, maximum veri çıkarma
"""

import re
import json
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse
import asyncio


class DeepAnalyzer:
    """
    3 Katmanlı Analiz:
    1. Deep Crawl - Her şeyi topla
    2. Pattern Matching - AI olmadan analiz
    3. AI Verification - Sadece belirsiz durumlar
    """
    
    # ============================================================
    # LAYER 1: AI MODEL SIGNATURES
    # ============================================================
    
    MODEL_SIGNATURES = {
        # OpenAI
        'gpt-4': {
            'patterns': [
                r'gpt-?4[o\-\w]*',
                r'chatgpt-?4',
                r'openai.*gpt-?4',
            ],
            'provider': 'openai',
            'confidence': 0.95
        },
        'gpt-3.5': {
            'patterns': [
                r'gpt-?3\.5[–\-]?turbo',
                r'chatgpt.*3\.5',
            ],
            'provider': 'openai',
            'confidence': 0.95
        },
        'dall-e': {
            'patterns': [
                r'dall[-\s]?e[-\s]?[23]?',
                r'dalle[-\s]?[23]?',
            ],
            'provider': 'openai',
            'confidence': 0.90
        },
        
        # Anthropic
        'claude-3': {
            'patterns': [
                r'claude[-\s]?3[.\-\s]?(opus|sonnet|haiku)',
                r'claude[-\s]?(opus|sonnet|haiku)',
            ],
            'provider': 'anthropic',
            'confidence': 0.95
        },
        'claude-2': {
            'patterns': [
                r'claude[-\s]?2[.\-\w]*',
                r'claude[-\s]?instant',
            ],
            'provider': 'anthropic',
            'confidence': 0.90
        },
        
        # Google
        'gemini': {
            'patterns': [
                r'gemini[-\s]?(pro|ultra|nano)',
                r'gemini[-\s]?1\.5',
                r'bard',
            ],
            'provider': 'google',
            'confidence': 0.95
        },
        'palm': {
            'patterns': [
                r'palm[-\s]?2?',
            ],
            'provider': 'google',
            'confidence': 0.85
        },
        
        # Meta
        'llama': {
            'patterns': [
                r'llama[-\s]?[23][-\s]?\d+b',
                r'llama[-\s]?3',
            ],
            'provider': 'meta',
            'confidence': 0.90
        },
        
        # Mistral
        'mixtral': {
            'patterns': [
                r'mixtral[-\s]?\d+x\d+b',
                r'mistral[-\s]?\d+b',
            ],
            'provider': 'mistral',
            'confidence': 0.90
        },
        
        # Stability AI
        'stable-diffusion': {
            'patterns': [
                r'stable[-\s]?diffusion[–\-\s]?(xl|3|2\.1)?',
                r'sdxl',
            ],
            'provider': 'stability',
            'confidence': 0.90
        },
        
        # Cohere
        'command': {
            'patterns': [
                r'command[-\s]?(r\+|nightly)',
            ],
            'provider': 'cohere',
            'confidence': 0.90
        },
    }
    
    # API Endpoint Patterns
    API_ENDPOINTS = {
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
        ],
        'replicate': [r'api\.replicate\.com'],
        'huggingface': [r'api-inference\.huggingface\.co'],
        'together': [r'api\.together\.xyz'],
        'groq': [r'api\.groq\.com'],
        'mistral': [r'api\.mistral\.ai'],
        'cohere': [r'api\.cohere\.ai'],
        'perplexity': [r'api\.perplexity\.ai'],
    }
    
    # JavaScript Library Signatures
    JS_LIBRARIES = {
        'openai-js': [r'openai[/-]', r'@openai/'],
        'langchain': [r'langchain', r'@langchain/'],
        'vercel-ai': [r'ai/react', r'@vercel/ai'],
        'anthropic-js': [r'@anthropic-ai/sdk'],
        'replicate-js': [r'replicate'],
        'transformers-js': [r'@xenova/transformers'],
    }
    
    # Marketing Keywords (claims)
    MARKETING_KEYWORDS = {
        'ai_powered': [
            r'ai[-\s]?powered',
            r'powered\s+by\s+ai',
            r'artificial\s+intelligence',
            r'machine\s+learning',
        ],
        'gpt_claim': [
            r'using\s+gpt',
            r'powered\s+by\s+gpt',
            r'based\s+on\s+gpt',
        ],
        'claude_claim': [
            r'using\s+claude',
            r'powered\s+by\s+claude',
        ],
        'free_tier': [
            r'free\s+tier',
            r'free\s+plan',
            r'no\s+credit\s+card',
            r'no\s+signup',
        ],
    }
    
    # ============================================================
    # MAIN ANALYSIS METHOD
    # ============================================================
    
    def analyze(self, crawl_data: Dict) -> Dict:
        """
        Deep analysis WITHOUT AI API
        Returns detailed intelligence report
        """
        
        url = crawl_data.get('url', '')
        domain = crawl_data.get('domain', '')
        title = crawl_data.get('title', '')
        page_text = crawl_data.get('page_text', '')
        network = crawl_data.get('network', {})
        
        # Collect all evidence
        evidence = {
            'models_detected': [],
            'providers_detected': set(),
            'api_calls': [],
            'js_libraries': [],
            'marketing_claims': [],
            'confidence_score': 0,
        }
        
        # 1. Network Analysis (highest confidence)
        self._analyze_network(network, evidence)
        
        # 2. Content Analysis (medium confidence)
        self._analyze_content(page_text, evidence)
        
        # 3. Title Analysis
        self._analyze_title(title, evidence)
        
        # 4. Calculate scores
        trust_score = self._calculate_trust_score(evidence)
        status = self._determine_status(evidence)
        category = self._determine_category(evidence, page_text)
        
        # 5. Build report
        report = {
            'url': url,
            'domain': domain,
            'title': title,
            'status': status,
            'trust_score': trust_score,
            'category': category,
            
            # Arsenal
            'detected_arsenal': self._build_arsenal(evidence),
            
            # Claims
            'marketing_claim': self._extract_claim(page_text, evidence),
            'actual_tech': self._describe_tech(evidence),
            
            # Capabilities
            'capabilities': self._detect_capabilities(page_text, evidence),
            
            # Pricing
            'pricing': self._detect_pricing(page_text),
            
            # Evidence
            'evidence_logs': self._build_evidence_logs(evidence),
            
            # Metadata
            'analysis_method': 'pattern_matching',
            'confidence': evidence['confidence_score'],
            'needs_ai_verification': evidence['confidence_score'] < 70,
        }
        
        return report
    
    # ============================================================
    # ANALYSIS METHODS
    # ============================================================
    
    def _analyze_network(self, network: Dict, evidence: Dict):
        """Analyze network traffic - HIGHEST CONFIDENCE"""
        
        # API calls from interceptor
        api_calls = network.get('ai_calls', [])
        for call in api_calls:
            provider = call.get('provider')
            evidence['providers_detected'].add(provider)
            evidence['api_calls'].append({
                'provider': provider,
                'url': call.get('url', ''),
                'confidence': 0.95
            })
        
        # Detected models
        models = network.get('detected_models', [])
        for model in models:
            evidence['models_detected'].append({
                'model': model,
                'source': 'network_traffic',
                'confidence': 0.90
            })
        
        # Boost confidence if network traffic detected
        if api_calls or models:
            evidence['confidence_score'] += 40
    
    def _analyze_content(self, text: str, evidence: Dict):
        """Analyze page content for AI signatures"""
        
        if not text:
            return
        
        text_lower = text.lower()
        
        # 1. Model signatures
        for model_name, config in self.MODEL_SIGNATURES.items():
            for pattern in config['patterns']:
                matches = re.findall(pattern, text_lower, re.IGNORECASE)
                if matches:
                    evidence['models_detected'].append({
                        'model': model_name,
                        'source': 'page_content',
                        'confidence': config['confidence'],
                        'matches': len(matches)
                    })
                    evidence['providers_detected'].add(config['provider'])
        
        # 2. API endpoints in content
        for provider, patterns in self.API_ENDPOINTS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    evidence['providers_detected'].add(provider)
                    evidence['confidence_score'] += 10
        
        # 3. Marketing claims
        for claim_type, patterns in self.MARKETING_KEYWORDS.items():
            for pattern in patterns:
                matches = re.findall(pattern, text_lower)
                if matches:
                    evidence['marketing_claims'].append({
                        'type': claim_type,
                        'matches': len(matches)
                    })
        
        # Boost confidence based on content
        if evidence['models_detected']:
            evidence['confidence_score'] += 30
    
    def _analyze_title(self, title: str, evidence: Dict):
        """Analyze page title"""
        if not title:
            return
        
        title_lower = title.lower()
        
        # AI keywords in title
        ai_keywords = ['ai', 'gpt', 'claude', 'chatbot', 'artificial intelligence']
        for keyword in ai_keywords:
            if keyword in title_lower:
                evidence['confidence_score'] += 5
                break
    
    # ============================================================
    # SCORING & CLASSIFICATION
    # ============================================================
    
    def _calculate_trust_score(self, evidence: Dict) -> int:
        """
        Calculate trust score based on transparency
        100 = Fully transparent
        50 = Vague
        0 = Misleading/Fake
        """
        
        score = 50  # Start neutral
        
        # Positive factors
        if evidence['api_calls']:
            score += 20  # Real API calls detected
        
        if evidence['models_detected']:
            score += 15  # Models explicitly mentioned
        
        if evidence['providers_detected']:
            score += 10
        
        # Negative factors
        has_ai_claim = any(c['type'] in ['ai_powered', 'gpt_claim'] 
                          for c in evidence['marketing_claims'])
        has_evidence = bool(evidence['models_detected'] or evidence['api_calls'])
        
        if has_ai_claim and not has_evidence:
            score -= 30  # Claims AI but no evidence
        
        return max(0, min(100, score))
    
    def _determine_status(self, evidence: Dict) -> str:
        """Determine verification status"""
        
        # Verified: Strong evidence of real AI usage
        if evidence['api_calls'] or len(evidence['models_detected']) >= 2:
            return 'verified'
        
        # Suspicious: Some evidence but unclear
        if evidence['models_detected'] or evidence['providers_detected']:
            return 'suspicious'
        
        # Fake: AI claims but no evidence
        has_claims = bool(evidence['marketing_claims'])
        if has_claims:
            return 'fake'
        
        return 'suspicious'
    
    def _determine_category(self, evidence: Dict, text: str) -> str:
        """Determine site category"""
        
        text_lower = text.lower()
        
        # Category keywords
        categories = {
            'Chatbot': ['chatbot', 'chat', 'conversation', 'assistant'],
            'Image Generation': ['image', 'photo', 'art', 'picture', 'generate image'],
            'Writing': ['writing', 'content', 'text', 'article', 'copy'],
            'Code': ['code', 'programming', 'developer', 'github', 'copilot'],
            'Video': ['video', 'clip', 'animation'],
            'Audio': ['audio', 'voice', 'speech', 'tts', 'music'],
            'Search': ['search', 'answer engine', 'research'],
        }
        
        # Score each category
        scores = {}
        for cat, keywords in categories.items():
            score = sum(text_lower.count(kw) for kw in keywords)
            if score > 0:
                scores[cat] = score
        
        # Return highest scoring category
        if scores:
            return max(scores, key=scores.get)
        
        return 'Other'
    
    # ============================================================
    # REPORT BUILDING
    # ============================================================
    
    def _build_arsenal(self, evidence: Dict) -> List[Dict]:
        """Build detected arsenal list"""
        
        arsenal = []
        seen = set()
        
        # From network
        for model in evidence['models_detected']:
            model_name = model['model']
            if model_name not in seen:
                seen.add(model_name)
                arsenal.append({
                    'model': model_name,
                    'type': self._get_provider(model_name),
                    'confidence': model['confidence'],
                    'source': model['source']
                })
        
        # From providers
        for provider in evidence['providers_detected']:
            key = f"provider_{provider}"
            if key not in seen:
                seen.add(key)
                arsenal.append({
                    'model': provider,
                    'type': provider,
                    'confidence': 0.80,
                    'source': 'api_detection'
                })
        
        return arsenal
    
    def _extract_claim(self, text: str, evidence: Dict) -> str:
        """Extract marketing claim"""
        
        if not text:
            return "No claim detected"
        
        # Look for AI-related claims in first 500 chars
        intro = text[:500].lower()
        
        # Common claim patterns
        patterns = [
            r'(powered by [^.]+)',
            r'(using [^.]+)',
            r'(based on [^.]+)',
            r'(leveraging [^.]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, intro, re.IGNORECASE)
            if match and any(kw in match.group(1).lower() 
                           for kw in ['ai', 'gpt', 'claude', 'model']):
                return match.group(1).strip().capitalize()
        
        # Generic claim
        if any(c['type'] == 'ai_powered' for c in evidence['marketing_claims']):
            return "Claims to be AI-powered"
        
        return "No explicit AI claim"
    
    def _describe_tech(self, evidence: Dict) -> str:
        """Describe actual technology detected"""
        
        if evidence['api_calls']:
            providers = list(evidence['providers_detected'])
            return f"Using {', '.join(providers)} API"
        
        if evidence['models_detected']:
            models = [m['model'] for m in evidence['models_detected'][:2]]
            return f"Mentions {', '.join(models)}"
        
        return "Technology unclear"
    
    def _detect_capabilities(self, text: str, evidence: Dict) -> List[str]:
        """Detect capabilities from content"""
        
        text_lower = text.lower()
        
        capabilities = []
        
        # Capability patterns
        checks = [
            ('CHATBOT', ['chat', 'conversation', 'message']),
            ('TEXT-TO-IMAGE', ['generate image', 'create art', 'ai art']),
            ('TEXT-TO-VIDEO', ['generate video', 'create video']),
            ('CODE-GENERATION', ['generate code', 'write code', 'coding assistant']),
            ('VOICE-GENERATION', ['text to speech', 'voice', 'tts']),
            ('WRITING', ['write', 'content', 'article', 'copywriting']),
            ('SEARCH', ['search', 'find', 'answer']),
        ]
        
        for cap, keywords in checks:
            if any(kw in text_lower for kw in keywords):
                capabilities.append(cap)
        
        return capabilities or ['GENERAL']
    
    def _detect_pricing(self, text: str) -> Dict:
        """Detect pricing information"""
        
        text_lower = text.lower()
        
        # Check for free tier
        if any(kw in text_lower for kw in ['free', 'no credit card', 'no signup']):
            if any(kw in text_lower for kw in ['paid', 'premium', 'pro', '$']):
                return {
                    'type': 'freemium',
                    'details': 'Free tier with paid upgrades'
                }
            return {
                'type': 'free',
                'details': 'Free to use'
            }
        
        # Check for paid
        if any(kw in text_lower for kw in ['$', 'pricing', 'subscribe', 'plan']):
            return {
                'type': 'paid',
                'details': 'Paid service'
            }
        
        return {
            'type': 'unknown',
            'details': 'Pricing not clear'
        }
    
    def _build_evidence_logs(self, evidence: Dict) -> List[str]:
        """Build evidence log entries"""
        
        logs = []
        
        # Network evidence
        if evidence['api_calls']:
            for call in evidence['api_calls'][:3]:
                logs.append(f"> API CALL: {call['provider']} detected ({call['confidence']:.0%} confidence)")
        
        # Model evidence
        for model in evidence['models_detected'][:3]:
            logs.append(f"> MODEL: {model['model']} found in {model['source']}")
        
        # Provider evidence
        if evidence['providers_detected']:
            providers = ', '.join(list(evidence['providers_detected'])[:3])
            logs.append(f"> PROVIDERS: {providers}")
        
        # Claims
        for claim in evidence['marketing_claims'][:2]:
            logs.append(f"> CLAIM: {claim['type'].replace('_', ' ').title()}")
        
        return logs[:5] or ["> No significant evidence found"]
    
    def _get_provider(self, model_name: str) -> str:
        """Get provider for model name"""
        for name, config in self.MODEL_SIGNATURES.items():
            if name in model_name.lower():
                return config['provider']
        return 'unknown'


# ============================================================
# ENHANCED CRAWLER INTEGRATION
# ============================================================

async def enhanced_crawl(url: str) -> Dict:
    """
    Enhanced crawling with maximum data extraction
    """
    from crawler.engine import ImperialCrawler
    
    async with ImperialCrawler() as crawler:
        # Standard crawl
        result = await crawler.analyze(url)
        
        # Add deep analysis
        analyzer = DeepAnalyzer()
        analysis = analyzer.analyze(result)
        
        return {
            'crawl_data': result,
            'analysis': analysis,
            'needs_ai': analysis.get('needs_ai_verification', False)
        }
