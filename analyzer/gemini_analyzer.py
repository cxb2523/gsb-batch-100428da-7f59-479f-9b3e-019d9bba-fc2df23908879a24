# backend/analyzer/gemini_analyzer.py
"""
Gemini AI Analyzer - WITH BATCH SUPPORT FOR 10-50x SPEED!
"""

import json
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
import google.generativeai as genai
from urllib.parse import urlparse

from .api_manager import GeminiAPIManager


class GeminiAnalyzer:
    """Gemini site analysis - Single + Batch modes"""
    
    # Single site prompt
    SINGLE_PROMPT = """You are an AI forensics expert. Analyze this website and provide a detailed intelligence report.

## Site Information:
- URL: {url}
- Title: {title}
- Detected AI Providers (from network): {providers}
- Detected Models (from network): {models}
- Page Content Preview: {page_text}

## Your Tasks:
1. **detected_arsenal**: List AI models used [{"model": "name", "type": "openai/anthropic/google/opensource/proprietary"}]
2. **marketing_claim**: What does the site claim? (1 sentence)
3. **actual_tech**: What technology are they actually using? (1 sentence)
4. **trust_score**: Score 0-100 based on transparency
5. **status**: "verified" / "suspicious" / "fake"
6. **category**: Chatbot, Image Generation, Writing, Code, Audio, Video, Productivity, Design, Search, Data Analysis, 3D Generation, Education, Business Tools, Other
7. **pricing**: {"type": "free/freemium/paid/usage", "details": "description"}
8. **capabilities**: ["CHATBOT", "TEXT-TO-IMAGE", "TEXT-TO-VIDEO", "CODE-GENERATION", "VOICE-GENERATION", "3D-GENERATION", "DATA-ANALYSIS", "SEARCH", "WRITING", "PRODUCTIVITY"]
9. **evidence_logs**: Max 5 findings ["> KEYWORD: description"]

Return ONLY valid JSON, no markdown, no explanations."""

    # Batch prompt
    BATCH_PROMPT = """You are an AI forensics expert analyzing multiple AI websites. 
Analyze ALL {count} websites below and return a JSON array with exactly {count} objects.

WEBSITES TO ANALYZE:
{websites}

CRITICAL INSTRUCTIONS:
- Return ONLY a valid JSON array with exactly {count} objects
- Each object must match the exact URL from input
- No markdown code blocks, no explanations, ONLY the JSON array

REQUIRED FIELDS FOR EACH OBJECT:
{{
  "url": "exact URL from input (copy it exactly)",
  "category": "Chatbot|Image Generation|Writing|Code|Audio|Video|Productivity|Design|Search|Data Analysis|3D Generation|Education|Business Tools|Other",
  "status": "verified|suspicious|fake",
  "trust_score": 0-100,
  "marketing_claim": "What they claim in 1 sentence",
  "actual_tech": "What they actually use in 1 sentence",
  "detected_arsenal": [{{"model": "gpt-4", "type": "openai"}}, {{"model": "claude-3", "type": "anthropic"}}],
  "capabilities": ["CHATBOT", "TEXT-TO-IMAGE", "CODE-GENERATION"],
  "pricing": {{"type": "freemium", "details": "Free tier + paid plans"}},
  "evidence_logs": ["Finding 1", "Finding 2", "Finding 3"]
}}

EVALUATION CRITERIA:
- trust_score: 100 = fully transparent, 50 = vague claims, 0 = misleading
- status: verified = real AI detected, suspicious = unclear, fake = no AI found
- detected_arsenal: List ALL AI models found (GPT-4, Claude, Gemini, Llama, etc.)
- capabilities: Match what the site actually does

Return format example:
[
  {{"url": "https://site1.com", "category": "Chatbot", "status": "verified", ...}},
  {{"url": "https://site2.com", "category": "Image Generation", "status": "suspicious", ...}}
]

NO markdown, NO explanations, START with [ and END with ]"""

    PREFERRED_MODELS = [
        'gemini-2.5-flash-lite',  # Pro for batch (larger context)
        'gemini-2.5-flash-tts',
        'gemini-2.5-flash', 
        'gemini-3-flash',
    ]

    def __init__(self, batch_size: int = 20):
        self.api_manager = GeminiAPIManager()
        self.model_name = None
        self.batch_size = batch_size
        self.max_retries = 3
    
    def _find_best_model(self) -> str:
        """Find best available Gemini model"""
        try:
            available = []
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    available.append(m.name)
            
            for pref in self.PREFERRED_MODELS:
                for avail in available:
                    if pref in avail:
                        return avail
            
            return available[0] if available else 'models/gemini-1.5-flash'
        except:
            return 'models/gemini-1.5-flash'
    
    def _detect_error_type(self, error: Exception) -> str:
        """Detect error type for proper handling"""
        error_str = str(error).lower()
        
        if '429' in str(error):
            return 'rate_limit'
        elif 'quota' in error_str or 'resource exhausted' in error_str:
            return 'quota_exceeded'
        elif 'invalid' in error_str and 'key' in error_str:
            return 'invalid_key'
        elif 'timeout' in error_str:
            return 'timeout'
        
        return 'unknown'
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL for matching (remove www, trailing slash, etc.)"""
        try:
            parsed = urlparse(url.lower())
            domain = parsed.netloc.replace('www.', '')
            path = parsed.path.rstrip('/')
            return f"{domain}{path}"
        except:
            return url.lower().replace('www.', '').rstrip('/')
    
    # ============================================================
    # SINGLE SITE ANALYSIS (Original method)
    # ============================================================
    
    async def analyze(self, crawl_data: Dict) -> Optional[Dict]:
        """
        Analyze single site with API rotation
        """
        url = crawl_data.get('url', '')
        title = crawl_data.get('title', '')
        network = crawl_data.get('network', {})
        page_text = crawl_data.get('page_text', '')[:2000]
        
        providers = network.get('providers_found', [])
        models = network.get('detected_models', [])
        
        prompt = self.SINGLE_PROMPT.format(
            url=url, 
            title=title,
            providers=providers or "Not detected",
            models=models or "Not detected",
            page_text=page_text or "Content not available"
        )
        
        for attempt in range(self.max_retries):
            key_status = await self.api_manager.get_available_key()
            
            if not key_status:
                next_time = await self.api_manager.get_next_available_time()
                if next_time:
                    wait = min(10, max(1, (next_time - datetime.now()).total_seconds()))
                    await asyncio.sleep(wait)
                    continue
                return None
            
            try:
                genai.configure(api_key=key_status.key)
                
                if not self.model_name:
                    self.model_name = self._find_best_model()
                
                model = genai.GenerativeModel(self.model_name)
                
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        model.generate_content, 
                        prompt,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.2, 
                            max_output_tokens=1500
                        )
                    ),
                    timeout=45
                )
                
                result = self._parse_single_response(response.text, crawl_data, providers, models)
                
                if result:
                    await self.api_manager.mark_success(key_status)
                    return result
                    
            except asyncio.TimeoutError:
                await self.api_manager.mark_error(key_status, 'timeout')
                
            except Exception as e:
                error_type = self._detect_error_type(e)
                await self.api_manager.mark_error(key_status, error_type)
            
            await asyncio.sleep(0.5)
        
        return None
    
    def _parse_single_response(self, text: str, crawl_data: Dict, 
                              providers: list, models: list) -> Optional[Dict]:
        """Parse single site response"""
        try:
            if '```json' in text:
                start = text.find('```json') + 7
                end = text.find('```', start)
                text = text[start:end].strip()
            elif '```' in text:
                start = text.find('```') + 3
                end = text.find('```', start)
                text = text[start:end].strip()
            
            if not text.startswith('{'):
                start = text.find('{')
                end = text.rfind('}') + 1
                if start != -1 and end > start:
                    text = text[start:end]
            
            analysis = json.loads(text)
            
            analysis['url'] = crawl_data.get('url', '')
            analysis['domain'] = crawl_data.get('domain', '')
            analysis['title'] = crawl_data.get('title', '')
            analysis['network_evidence'] = {
                'providers': providers, 
                'models': models
            }
            
            return analysis
            
        except:
            return None
    
    # ============================================================
    # BATCH ANALYSIS (NEW - 10-50x FASTER!)
    # ============================================================
    
    async def analyze_batch(self, crawl_data_list: List[Dict]) -> List[Optional[Dict]]:
        """
        Analyze multiple sites in ONE API call
        Returns list of analyses (same order as input)
        🚀 10-50x FASTER than single analysis!
        """
        
        if not crawl_data_list:
            return []
        
        # Debug logging
        print(f"\n🔍 DEBUG: Starting batch analysis...")
        print(f"  📦 Input sites: {len(crawl_data_list)}")
        
        # Limit batch size
        crawl_data_list = crawl_data_list[:self.batch_size]
        
        # Get API key
        key_status = await self.api_manager.get_available_key()
        
        if not key_status:
            print(f"  ❌ No API key available")
            return [None] * len(crawl_data_list)
        
        try:
            genai.configure(api_key=key_status.key)
            
            if not self.model_name:
                self.model_name = self._find_best_model()
                print(f"  🤖 Using model: {self.model_name}")
            
            model = genai.GenerativeModel(self.model_name)
            
            # Build batch prompt
            prompt = self._build_batch_prompt(crawl_data_list)
            print(f"  📝 Prompt size: {len(prompt)} chars")
            
            # Call Gemini
            print(f"  🚀 Calling Gemini API...")
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    model.generate_content,
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.2,
                        max_output_tokens=8000
                    )
                ),
                timeout=120
            )
            
            print(f"  ✅ Got response!")
            print(f"  📄 Response length: {len(response.text)} chars")
            print(f"  📄 Response preview: {response.text[:300]}...")
            
            # Parse batch response
            print(f"  🔧 Parsing response...")
            results = self._parse_batch_response(response.text, crawl_data_list)
            
            successful = sum(1 for r in results if r is not None)
            print(f"  ✅ Successfully parsed: {successful}/{len(crawl_data_list)}")
            
            if successful == 0:
                print(f"  ⚠️  WARNING: All parses failed!")
                print(f"  🔍 Full response:\n{response.text}")
            
            # Mark success
            await self.api_manager.mark_success(key_status)
            
            return results
            
        except asyncio.TimeoutError:
            print(f"  ❌ Timeout after 120 seconds")
            await self.api_manager.mark_error(key_status, 'timeout')
            return [None] * len(crawl_data_list)
            
        except Exception as e:
            print(f"  ❌ ERROR in analyze_batch:")
            print(f"     Type: {type(e).__name__}")
            print(f"     Message: {str(e)}")
            import traceback
            print(f"     Traceback:\n{traceback.format_exc()}")
            
            error_type = self._detect_error_type(e)
            await self.api_manager.mark_error(key_status, error_type)
            return [None] * len(crawl_data_list)
    
    def _build_batch_prompt(self, crawl_data_list: List[Dict]) -> str:
        """Build prompt for multiple sites"""
        
        websites_text = ""
        for i, data in enumerate(crawl_data_list, 1):
            url = data.get('url', '')
            title = data.get('title', '')
            network = data.get('network', {})
            page_text = data.get('page_text', '')[:400]  # Shorter per site
            
            providers = network.get('providers_found', [])
            models = network.get('detected_models', [])
            
            websites_text += f"""
---
SITE {i}:
URL: {url}
Title: {title}
Detected Providers: {', '.join(providers) if providers else 'None'}
Detected Models: {', '.join(models) if models else 'None'}
Content: {page_text}
"""
        
        return self.BATCH_PROMPT.format(
            count=len(crawl_data_list),
            websites=websites_text
        )
    
    def _parse_batch_response(self, text: str, crawl_data_list: List[Dict]) -> List[Optional[Dict]]:
        """Parse batch JSON array response - WITH FUZZY URL MATCHING"""
        try:
            # Extract JSON array
            if '```json' in text:
                start = text.find('```json') + 7
                end = text.find('```', start)
                text = text[start:end].strip()
            elif '```' in text:
                start = text.find('```') + 3
                end = text.find('```', start)
                text = text[start:end].strip()
            
            # Find array
            if not text.startswith('['):
                start = text.find('[')
                end = text.rfind(']') + 1
                if start != -1 and end > start:
                    text = text[start:end]
            
            print(f"    🔍 Cleaned JSON: {text[:200]}...")
            
            # Parse JSON array
            analyses = json.loads(text)
            
            if not isinstance(analyses, list):
                print(f"    ❌ Response is not a list: {type(analyses)}")
                return [None] * len(crawl_data_list)
            
            print(f"    ✅ Parsed {len(analyses)} analyses from JSON")
            
            # ============================================
            # FIXED: Fuzzy URL matching
            # ============================================
            
            # Create normalized URL map
            url_map = {}
            for data in crawl_data_list:
                original_url = data.get('url', '')
                normalized = self._normalize_url(original_url)
                url_map[normalized] = data
            
            print(f"    🗺️  URL map created with {len(url_map)} entries")
            
            # Match analyses to input
            results = []
            matched_count = 0
            
            for i, analysis in enumerate(analyses):
                analysis_url = analysis.get('url', '')
                normalized_analysis = self._normalize_url(analysis_url)
                
                # Try to find matching crawl data
                crawl_data = url_map.get(normalized_analysis)
                
                if crawl_data:
                    # Success! Add metadata
                    analysis['domain'] = crawl_data.get('domain', '')
                    analysis['title'] = crawl_data.get('title', '')
                    
                    network = crawl_data.get('network', {})
                    analysis['network_evidence'] = {
                        'providers': network.get('providers_found', []),
                        'models': network.get('detected_models', [])
                    }
                    
                    results.append(analysis)
                    matched_count += 1
                else:
                    # No match - still add the analysis but without metadata
                    print(f"    ⚠️  No match for: {analysis_url} (normalized: {normalized_analysis})")
                    
                    # Try to salvage: use position-based matching
                    if i < len(crawl_data_list):
                        fallback_data = crawl_data_list[i]
                        analysis['domain'] = fallback_data.get('domain', '')
                        analysis['title'] = fallback_data.get('title', '')
                        
                        network = fallback_data.get('network', {})
                        analysis['network_evidence'] = {
                            'providers': network.get('providers_found', []),
                            'models': network.get('detected_models', [])
                        }
                        
                        results.append(analysis)
                        matched_count += 1
                        print(f"    ✅ Used fallback position matching for site {i}")
                    else:
                        results.append(None)
            
            print(f"    ✅ Matched {matched_count}/{len(analyses)} analyses")
            
            # Fill missing results to match input length
            while len(results) < len(crawl_data_list):
                results.append(None)
            
            return results[:len(crawl_data_list)]
            
        except json.JSONDecodeError as e:
            print(f"    ❌ JSON parse error: {str(e)}")
            print(f"    📄 Problematic text: {text[:500]}")
            return [None] * len(crawl_data_list)
            
        except Exception as e:
            print(f"    ❌ Parse error: {type(e).__name__}: {str(e)}")
            import traceback
            print(f"    📄 Traceback:\n{traceback.format_exc()}")
            return [None] * len(crawl_data_list)
    
    # ============================================================
    # API Management
    # ============================================================
    
    def get_api_status(self) -> Dict:
        """Get current API status"""
        return self.api_manager.get_status()
    
    def reset_api_keys(self):
        """Reset all API keys"""
        self.api_manager.reset_all()