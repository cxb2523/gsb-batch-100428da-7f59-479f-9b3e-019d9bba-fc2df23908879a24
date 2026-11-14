# core/payment_detector.py
"""
🔥 PAYMENT DETECTOR - Ödeme sistemleri ve pricing tespiti
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class PricingPlan:
    """Pricing plan bilgisi"""
    name: str
    price: str
    period: str  # 'monthly', 'yearly', 'one-time', 'usage'
    features: List[str]
    is_free: bool


class PaymentDetector:
    """
    Ödeme sistemleri ve pricing tespiti.
    Tespit edilen:
    - Payment providers (Stripe, PayPal, etc.)
    - Pricing model (free, freemium, paid)
    - Pricing plans
    - Subscription types
    """
    
    # Payment Provider Patterns
    PAYMENT_PATTERNS = {
        'stripe': {
            'scripts': [r'js\.stripe\.com', r'stripe\.js', r'@stripe/'],
            'forms': [r'stripe', r'card-element'],
            'content': [r'stripe\.com', r'powered by stripe'],
            'cookies': [r'__stripe'],
        },
        'paypal': {
            'scripts': [r'paypal\.com/sdk', r'paypalobjects\.com'],
            'content': [r'paypal', r'pay with paypal'],
            'forms': [r'paypal'],
        },
        'paddle': {
            'scripts': [r'paddle\.js', r'cdn\.paddle\.com'],
            'content': [r'paddle\.com'],
        },
        'lemonsqueezy': {
            'scripts': [r'lemon\.js', r'lemonsqueezy'],
            'content': [r'lemonsqueezy', r'lemon squeezy'],
        },
        'gumroad': {
            'scripts': [r'gumroad\.com'],
            'content': [r'gumroad'],
        },
        'chargebee': {
            'scripts': [r'chargebee\.js', r'js\.chargebee\.com'],
            'content': [r'chargebee'],
        },
        'recurly': {
            'scripts': [r'recurly\.js', r'js\.recurly\.com'],
            'content': [r'recurly'],
        },
        'braintree': {
            'scripts': [r'braintree', r'braintreegateway'],
            'content': [r'braintree'],
        },
        'square': {
            'scripts': [r'squareup\.com', r'square\.js'],
            'content': [r'square payments'],
        },
        'razorpay': {
            'scripts': [r'razorpay\.com', r'razorpay\.js'],
            'content': [r'razorpay'],
        },
        'fastspring': {
            'scripts': [r'fastspring'],
            'content': [r'fastspring'],
        },
        'appsumo': {
            'scripts': [],
            'content': [r'appsumo', r'lifetime deal'],
        },
    }
    
    # Pricing Model Indicators
    PRICING_INDICATORS = {
        'free': {
            'strong': [r'100%\s*free', r'completely free', r'always free', r'forever free'],
            'medium': [r'free to use', r'free plan', r'free tier', r'no credit card', r'no payment'],
            'weak': [r'\bfree\b'],
        },
        'freemium': {
            'strong': [r'free\s*(?:and|&)\s*(?:paid|premium)', r'upgrade to (?:pro|premium)', r'free tier.*paid'],
            'medium': [r'start free', r'try free', r'free trial', r'premium features'],
        },
        'subscription': {
            'strong': [r'/(?:month|mo|year|yr)\b', r'billed (?:monthly|annually|yearly)'],
            'medium': [r'subscription', r'subscribe', r'recurring'],
        },
        'usage': {
            'strong': [r'per (?:request|call|token|credit|generation)', r'pay as you go', r'usage[- ]based'],
            'medium': [r'credits?', r'tokens?', r'api calls?'],
        },
        'one_time': {
            'strong': [r'one[- ]time (?:payment|purchase)', r'lifetime (?:access|deal)', r'pay once'],
            'medium': [r'single payment', r'no subscription'],
        },
    }
    
    # Price Extraction Patterns
    PRICE_PATTERNS = [
        # $X.XX/mo
        (r'\$(\d+(?:\.\d{2})?)\s*/\s*(?:mo|month)', 'monthly'),
        (r'\$(\d+(?:\.\d{2})?)\s*/\s*(?:yr|year|annually)', 'yearly'),
        # $X monthly
        (r'\$(\d+(?:\.\d{2})?)\s*(?:per\s*)?month', 'monthly'),
        (r'\$(\d+(?:\.\d{2})?)\s*(?:per\s*)?year', 'yearly'),
        # X.XX USD/EUR
        (r'(\d+(?:\.\d{2})?)\s*(?:USD|EUR|GBP)\s*/\s*mo', 'monthly'),
        # Free
        (r'\bfree\s*(?:plan|tier)?\b', 'free'),
        # Starting at
        (r'(?:starting|from)\s*\$(\d+(?:\.\d{2})?)', 'starting'),
    ]
    
    # Plan Name Patterns
    PLAN_NAMES = [
        'free', 'basic', 'starter', 'hobby', 'personal', 'individual',
        'pro', 'professional', 'plus', 'premium', 'advanced',
        'business', 'team', 'teams', 'enterprise', 'company',
        'unlimited', 'ultimate', 'agency',
    ]
    
    def detect(self, crawl_data: Dict) -> Dict:
        """
        Ödeme ve pricing tespiti yap
        """
        result = {
            'payment_providers': [],
            'pricing_model': 'unknown',
            'pricing_confidence': 0,
            'has_free_tier': False,
            'plans': [],
            'prices_found': [],
            'evidence': [],
        }
        
        # 1. Payment Provider Detection
        result['payment_providers'] = self._detect_providers(crawl_data)
        
        # 2. Pricing Model Detection
        pricing_result = self._detect_pricing_model(crawl_data)
        result['pricing_model'] = pricing_result['model']
        result['pricing_confidence'] = pricing_result['confidence']
        result['has_free_tier'] = pricing_result['has_free']
        result['evidence'].extend(pricing_result['evidence'])
        
        # 3. Price Extraction
        result['prices_found'] = self._extract_prices(crawl_data)
        
        # 4. Plan Detection
        result['plans'] = self._detect_plans(crawl_data)
        
        # 5. Pricing Page Analysis
        pricing_page = crawl_data.get('pricing_page_data', {})
        if pricing_page:
            result['pricing_page'] = self._analyze_pricing_page(pricing_page)
        
        return result
    
    def _detect_providers(self, data: Dict) -> List[Dict]:
        """Payment provider tespiti"""
        providers = []
        
        # Script URLs
        script_urls = data.get('scripts', {}).get('external_urls', [])
        script_urls = [s.lower() for s in script_urls]
        
        # Script content
        script_content = ' '.join(
            s.get('code', '') for s in data.get('scripts', {}).get('inline', [])
        ).lower()
        
        # Page content
        page_content = data.get('content', {}).get('page_text', '').lower()
        
        # Cookies
        cookies = [c.get('name', '').lower() for c in data.get('storage', {}).get('cookies', [])]
        
        for provider, patterns in self.PAYMENT_PATTERNS.items():
            confidence = 0
            evidence = []
            
            # Check scripts
            for pattern in patterns.get('scripts', []):
                for url in script_urls:
                    if re.search(pattern, url, re.IGNORECASE):
                        confidence += 0.4
                        evidence.append(f"Script: {url[:50]}")
                        break
                if re.search(pattern, script_content, re.IGNORECASE):
                    confidence += 0.3
                    evidence.append(f"SDK detected")
            
            # Check content
            for pattern in patterns.get('content', []):
                if re.search(pattern, page_content, re.IGNORECASE):
                    confidence += 0.2
                    evidence.append(f"Content mention")
            
            # Check cookies
            for pattern in patterns.get('cookies', []):
                for cookie in cookies:
                    if re.search(pattern, cookie, re.IGNORECASE):
                        confidence += 0.3
                        evidence.append(f"Cookie: {cookie}")
            
            if confidence > 0.2:
                providers.append({
                    'name': provider,
                    'confidence': min(1.0, confidence),
                    'evidence': evidence[:3],
                })
        
        return sorted(providers, key=lambda x: x['confidence'], reverse=True)
    
    def _detect_pricing_model(self, data: Dict) -> Dict:
        """Pricing model tespiti"""
        content = data.get('content', {}).get('page_text', '').lower()
        pricing_page = data.get('pricing_page_data', {})
        
        all_text = content
        if pricing_page:
            all_text += ' ' + str(pricing_page)
        
        scores = {}
        evidence = []
        
        for model, indicators in self.PRICING_INDICATORS.items():
            score = 0
            
            for pattern in indicators.get('strong', []):
                if re.search(pattern, all_text, re.IGNORECASE):
                    score += 0.5
                    evidence.append(f"{model}: strong match")
            
            for pattern in indicators.get('medium', []):
                if re.search(pattern, all_text, re.IGNORECASE):
                    score += 0.3
            
            for pattern in indicators.get('weak', []):
                if re.search(pattern, all_text, re.IGNORECASE):
                    score += 0.1
            
            if score > 0:
                scores[model] = min(1.0, score)
        
        # Determine primary model
        if not scores:
            return {'model': 'unknown', 'confidence': 0, 'has_free': False, 'evidence': []}
        
        # Has free tier?
        has_free = 'free' in scores or 'freemium' in scores
        
        # Best model
        best_model = max(scores, key=scores.get)
        
        # Combine freemium
        if 'free' in scores and ('subscription' in scores or 'usage' in scores):
            best_model = 'freemium'
            scores['freemium'] = max(scores.values())
        
        return {
            'model': best_model,
            'confidence': round(scores.get(best_model, 0) * 100),
            'has_free': has_free,
            'evidence': evidence[:5],
            'all_scores': scores,
        }
    
    def _extract_prices(self, data: Dict) -> List[Dict]:
        """Fiyatları çıkar"""
        prices = []
        
        content = data.get('content', {}).get('page_text', '')
        pricing_page = data.get('pricing_page_data', {})
        
        all_text = content
        if pricing_page:
            all_text += ' ' + str(pricing_page)
        
        for pattern, period in self.PRICE_PATTERNS:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            for match in matches[:5]:
                if isinstance(match, tuple):
                    match = match[0]
                
                if period == 'free':
                    prices.append({'amount': '0', 'period': 'free', 'raw': 'Free'})
                else:
                    prices.append({
                        'amount': match,
                        'period': period,
                        'raw': f"${match}/{period[:2]}",
                    })
        
        # Deduplicate
        seen = set()
        unique = []
        for p in prices:
            key = f"{p['amount']}_{p['period']}"
            if key not in seen:
                seen.add(key)
                unique.append(p)
        
        return unique[:10]
    
    def _detect_plans(self, data: Dict) -> List[Dict]:
        """Plan isimleri tespit et"""
        plans = []
        
        content = data.get('content', {}).get('page_text', '').lower()
        headings = data.get('content', {}).get('headings', [])
        
        # Heading'lerde plan isimleri
        for heading in headings:
            text = heading.get('text', '').lower()
            for plan_name in self.PLAN_NAMES:
                if plan_name in text:
                    plans.append({
                        'name': plan_name.title(),
                        'source': 'heading',
                    })
        
        # Content'te plan + price pattern
        plan_price_pattern = r'(' + '|'.join(self.PLAN_NAMES) + r')\s*(?:plan|tier)?\s*[\:\-]?\s*\$?(\d+(?:\.\d{2})?)'
        
        matches = re.findall(plan_price_pattern, content, re.IGNORECASE)
        for plan, price in matches:
            plans.append({
                'name': plan.title(),
                'price': f"${price}" if price else '',
                'source': 'content',
            })
        
        # Deduplicate
        seen = set()
        unique = []
        for p in plans:
            if p['name'] not in seen:
                seen.add(p['name'])
                unique.append(p)
        
        return unique[:8]
    
    def _analyze_pricing_page(self, pricing_data: Dict) -> Dict:
        """Pricing page'i analiz et"""
        return {
            'url': pricing_data.get('url', ''),
            'plans_found': pricing_data.get('plans', []),
            'prices_found': pricing_data.get('prices', []),
        }