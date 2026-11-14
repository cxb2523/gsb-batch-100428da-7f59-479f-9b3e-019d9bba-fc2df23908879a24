# core/analyzer.py
"""
🔥 MAIN ANALYZER - Tüm verileri birleştir ve rapor üret
"""

from typing import Dict, List, Optional
from datetime import datetime

from .ai_detector import AIDetector
from .payment_detector import PaymentDetector


class IntelligenceAnalyzer:
    """
    Ana analiz motoru.
    Crawler'dan gelen veriyi alır, tüm detektörlerden geçirir,
    final rapor üretir.
    """
    
    def __init__(self):
        self.ai_detector = AIDetector()
        self.payment_detector = PaymentDetector()
    
    def analyze(self, crawl_data: Dict) -> Dict:
        """
        Tam analiz yap ve intelligence report üret
        """
        url = crawl_data.get('url', '')
        domain = crawl_data.get('domain', '')
        title = crawl_data.get('content', {}).get('title', '')
        
        # 1. AI Detection
        ai_result = self.ai_detector.detect(crawl_data)
        
        # 2. Payment Detection
        payment_result = self.payment_detector.detect(crawl_data)
        
        # 3. Category Detection
        category = self._detect_category(crawl_data, ai_result)
        
        # 4. Capabilities Detection
        capabilities = self._detect_capabilities(crawl_data, ai_result)
        
        # 5. Trust Score Calculation
        trust_score = self._calculate_trust_score(ai_result, payment_result, crawl_data)
        
        # 6. Status Determination
        status = self._determine_status(ai_result, trust_score)
        
        # 7. Build Final Report
        report = {
            # Basic info
            'url': url,
            'domain': domain,
            'name': title or domain,
            'title': title,
            
            # Status
            'status': status,
            'trust_score': trust_score,
            
            # Category
            'category': category,
            'capabilities': capabilities,
            
            # AI Analysis
            'detected_arsenal': self._build_arsenal(ai_result),
            'ai_providers': [p['name'] for p in ai_result.get('providers', [])],
            'ai_confidence': ai_result.get('total_confidence', 0),
            'has_ai': ai_result.get('has_ai', False),
            
            # Payment Analysis
            'pricing': {
                'type': payment_result.get('pricing_model', 'unknown'),
                'has_free_tier': payment_result.get('has_free_tier', False),
                'plans': payment_result.get('plans', []),
                'prices': payment_result.get('prices_found', []),
                'payment_providers': [p['name'] for p in payment_result.get('payment_providers', [])],
            },
            
            # Evidence
            'evidence_logs': self._build_evidence_logs(ai_result, payment_result, crawl_data),
            
            # Marketing
            'marketing_claim': self._extract_marketing_claim(crawl_data),
            'actual_tech': self._describe_actual_tech(ai_result),
            
            # Libraries & Tech
            'detected_libraries': crawl_data.get('scripts', {}).get('detected_libraries', []),
            
            # Metadata
            'last_verified': datetime.now().isoformat(),
            'analysis_version': '3.0',
            'crawl_success': crawl_data.get('success', False),
        }
        
        return report
    
    def _detect_category(self, data: Dict, ai_result: Dict) -> str:
        """Kategori tespit et"""
        content = data.get('content', {}).get('page_text', '').lower()
        title = data.get('content', {}).get('title', '').lower()
        all_text = f"{title} {content}"
        
        categories = {
            'Chatbot': ['chatbot', 'chat assistant', 'ai chat', 'conversational ai', 'talk to ai'],
            'Image Generation': ['image generat', 'text to image', 'ai art', 'picture generat', 'image creator', 'art generator'],
            'Writing': ['writing assistant', 'ai writer', 'content generat', 'copywriting', 'text generat', 'article writer'],
            'Code': ['code assistant', 'ai coding', 'code generat', 'programming', 'developer tool', 'copilot'],
            'Video': ['video generat', 'text to video', 'ai video', 'video creator', 'video editor'],
            'Audio': ['voice generat', 'text to speech', 'ai voice', 'music generat', 'audio', 'tts'],
            'Search': ['ai search', 'answer engine', 'research assistant', 'knowledge base'],
            'Design': ['design tool', 'ui design', 'graphic design', 'logo', 'creative tool'],
            'Productivity': ['productivity', 'automation', 'workflow', 'meeting', 'notes', 'assistant'],
            'Education': ['learning', 'tutor', 'education', 'study', 'course'],
            'Business': ['business', 'enterprise', 'crm', 'sales', 'marketing', 'analytics'],
            'Translation': ['translat', 'language', 'multilingual'],
            'Data Analysis': ['data analy', 'spreadsheet', 'visualization', 'dashboard'],
            '3D Generation': ['3d generat', 'text to 3d', '3d model'],
        }
        
        scores = {}
        for cat, keywords in categories.items():
            score = sum(2 if kw in title else 1 for kw in keywords if kw in all_text)
            if score > 0:
                scores[cat] = score
        
        return max(scores, key=scores.get) if scores else 'Other'
    
    def _detect_capabilities(self, data: Dict, ai_result: Dict) -> List[str]:
        """Yetenek tespit et"""
        caps = []
        content = data.get('content', {}).get('page_text', '').lower()
        
        checks = [
            ('CHATBOT', ['chat', 'conversation', 'assistant', 'ask']),
            ('TEXT-TO-IMAGE', ['generate image', 'create image', 'text to image', 'ai art', 'image generator']),
            ('TEXT-TO-VIDEO', ['generate video', 'text to video', 'video generator']),
            ('TEXT-TO-AUDIO', ['text to speech', 'tts', 'voice generator', 'audio']),
            ('TEXT-TO-3D', ['text to 3d', '3d generator', '3d model']),
            ('CODE-GENERATION', ['code generator', 'write code', 'programming assistant']),
            ('WRITING', ['writing assistant', 'article', 'content creator', 'copywriting']),
            ('TRANSLATION', ['translate', 'translation', 'multilingual']),
            ('SEARCH', ['search', 'research', 'find information']),
            ('DATA-ANALYSIS', ['analyze data', 'spreadsheet', 'visualization']),
            ('VOICE-CLONE', ['voice clone', 'clone voice']),
            ('IMAGE-EDIT', ['edit image', 'image editing', 'photo editor']),
            ('AVATAR', ['avatar', 'virtual human', 'digital human']),
            ('MUSIC', ['music generator', 'create music', 'song']),
        ]
        
        for cap, keywords in checks:
            if any(kw in content for kw in keywords):
                caps.append(cap)
        
        return caps or ['GENERAL']
    
    def _calculate_trust_score(self, ai_result: Dict, payment_result: Dict, data: Dict) -> int:
        """Trust score hesapla (0-100)"""
        score = 50  # Base score
        
        # AI Evidence
        ai_confidence = ai_result.get('total_confidence', 0)
        if ai_confidence >= 80:
            score += 25
        elif ai_confidence >= 50:
            score += 15
        elif ai_confidence >= 30:
            score += 5
        elif ai_confidence < 20:
            score -= 10
        
        # Network API calls detected = high trust
        providers = ai_result.get('providers', [])
        for p in providers:
            if 'network' in p.get('sources', []):
                score += 10
                break
        
        # Payment provider = legitimate business
        if payment_result.get('payment_providers'):
            score += 10
        
        # Clear pricing = transparent
        if payment_result.get('pricing_model') != 'unknown':
            score += 5
        
        # Has free tier = accessible
        if payment_result.get('has_free_tier'):
            score += 5
        
        # Crawl errors = suspicious
        errors = data.get('errors', [])
        if len(errors) > 3:
            score -= 10
        
        # Detected libraries = real product
        libraries = data.get('scripts', {}).get('detected_libraries', [])
        if any(lib in libraries for lib in ['stripe', 'openai-sdk', 'vercel-ai', 'langchain']):
            score += 10
        
        return max(0, min(100, score))
    
    def _determine_status(self, ai_result: Dict, trust_score: int) -> str:
        """Status belirle"""
        ai_confidence = ai_result.get('total_confidence', 0)
        has_ai = ai_result.get('has_ai', False)
        
        if ai_confidence >= 70 and trust_score >= 60:
            return 'verified'
        elif ai_confidence >= 40 or trust_score >= 50:
            return 'suspicious'
        elif not has_ai and ai_confidence < 20:
            return 'fake'
        else:
            return 'suspicious'
    
    def _build_arsenal(self, ai_result: Dict) -> List[Dict]:
        """AI arsenal listesi oluştur"""
        arsenal = []
        
        for model_info in ai_result.get('models', []):
            arsenal.append({
                'model': model_info.get('model', ''),
                'type': model_info.get('provider', 'unknown'),
                'confidence': round(model_info.get('confidence', 0) * 100),
                'source': model_info.get('source', ''),
            })
        
        # Provider'lardan da ekle
        for provider in ai_result.get('providers', []):
            for model in provider.get('models', []):
                if not any(a['model'] == model for a in arsenal):
                    arsenal.append({
                        'model': model,
                        'type': provider['name'],
                        'confidence': round(provider['confidence'] * 100),
                        'source': 'provider',
                    })
        
        return arsenal[:10]
    
    def _build_evidence_logs(self, ai_result: Dict, payment_result: Dict, data: Dict) -> List[str]:
        """Evidence logs oluştur"""
        logs = []
        
        # AI evidence
        logs.extend(ai_result.get('evidence_logs', [])[:5])
        
        # Payment evidence
        for provider in payment_result.get('payment_providers', [])[:2]:
            logs.append(f"> [PAYMENT] {provider['name']}: {', '.join(provider.get('evidence', []))[:50]}")
        
        # Pricing
        pricing = payment_result.get('pricing_model', 'unknown')
        if pricing != 'unknown':
            logs.append(f"> [PRICING] Model: {pricing}")
        
        # Libraries
        libs = data.get('scripts', {}).get('detected_libraries', [])
        if libs:
            logs.append(f"> [TECH] Libraries: {', '.join(libs[:5])}")
        
        return logs[:10]
    
    def _extract_marketing_claim(self, data: Dict) -> str:
        """Marketing claim çıkar"""
        content = data.get('content', {}).get('page_text', '')[:2000].lower()
        
        patterns = [
            r'(powered by [^.]{5,60})',
            r'(built (?:on|with) [^.]{5,60})',
            r'(using [^.]{5,60})',
            r'(leveraging [^.]{5,60})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                claim = match.group(1)
                if any(kw in claim for kw in ['ai', 'gpt', 'claude', 'llm', 'model', 'ml']):
                    return claim.strip()[:100].title()
        
        return "No explicit claim"
    
    def _describe_actual_tech(self, ai_result: Dict) -> str:
        """Gerçek teknoloji açıklaması"""
        providers = ai_result.get('providers', [])
        models = ai_result.get('models', [])
        
        if providers:
            top_providers = [p['name'] for p in providers[:2]]
            top_models = [m['model'] for m in models[:2]]
            
            if top_models:
                return f"Uses {', '.join(top_providers)} ({', '.join(top_models)})"
            else:
                return f"Uses {', '.join(top_providers)} API"
        
        return "Technology unclear"


# Eski format uyumluluğu için import
import re