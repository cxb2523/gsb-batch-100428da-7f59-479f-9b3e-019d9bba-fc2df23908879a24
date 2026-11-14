# ============================================================
# analyzer/api_manager.py - ASYNC COMPATIBLE VERSION
# ============================================================
"""
FIXED: Gemini API Key Manager with PROPER ROTATION + ASYNC SUPPORT
"""

import os
import time
import asyncio
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Reduced cooldown for faster processing
COOLDOWN_DURATION = 30  # was 60


@dataclass
class KeyStatus:
    """Status of an API key"""
    id: int
    key: str
    available: bool
    cooldown_until: float
    requests: int
    success: int
    failures: int
    last_used: float
    active: bool


class GeminiAPIManager:
    """Manages multiple Gemini API keys with round-robin rotation"""
    
    def __init__(self):
        self.keys: List[KeyStatus] = self._load_keys()
        self.current_index = 0  # Track current key for rotation
        
        if not self.keys:
            raise ValueError("No Gemini API keys found in .env")
        
        print(f"✅ API Manager initialized with {len(self.keys)} keys")
    
    def _load_keys(self) -> List[KeyStatus]:
        """Load all API keys from environment"""
        keys_data = []
        
        for i in range(10):  # Support up to 10 keys
            key = os.getenv(f'GEMINI_API_KEY_{i}')
            if key:
                keys_data.append(KeyStatus(
                    id=i,
                    key=key,
                    available=True,
                    cooldown_until=0,
                    requests=0,
                    success=0,
                    failures=0,
                    last_used=0,
                    active=True
                ))
        
        return keys_data
    
    async def get_available_key(self) -> Optional[KeyStatus]:
        """
        Get next available API key using round-robin with cooldown
        THIS IS THE KEY FIX - PROPER ROTATION!
        """
        attempts = 0
        max_attempts = len(self.keys) * 2
        
        while attempts < max_attempts:
            # Get current key
            key_status = self.keys[self.current_index]
            current_time = time.time()
            
            # Check if key is available (not in cooldown)
            if key_status.active and current_time >= key_status.cooldown_until:
                # Mark as available
                key_status.available = True
                
                # Move to next key for next call (ROUND-ROBIN)
                self.current_index = (self.current_index + 1) % len(self.keys)
                
                return key_status
            else:
                # Key in cooldown, mark as unavailable
                key_status.available = False
            
            # Move to next key
            self.current_index = (self.current_index + 1) % len(self.keys)
            attempts += 1
            
            # If all keys checked once, wait a bit
            if attempts % len(self.keys) == 0:
                await asyncio.sleep(1)
        
        # No keys available
        return None
    
    async def mark_success(self, key_status: KeyStatus):
        """Mark successful API call"""
        key_status.requests += 1
        key_status.success += 1
        key_status.last_used = time.time()
    
    async def mark_error(self, key_status: KeyStatus, error_type: str):
        """Mark failed API call and apply cooldown if needed"""
        key_status.requests += 1
        key_status.failures += 1
        key_status.last_used = time.time()
        
        # Apply cooldown for rate limits and quota errors
        if error_type in ['rate_limit', 'quota_exceeded', '429']:
            key_status.cooldown_until = time.time() + COOLDOWN_DURATION
            key_status.available = False
            print(f"⏸️  API Key {key_status.id} → COOLDOWN {COOLDOWN_DURATION}s (reason: {error_type})")
        
        # Disable key for invalid key errors
        elif error_type == 'invalid_key':
            key_status.active = False
            key_status.available = False
            print(f"❌ API Key {key_status.id} → DISABLED (invalid)")
    
    async def get_next_available_time(self) -> Optional[datetime]:
        """Get when next key will be available"""
        current_time = time.time()
        min_wait = float('inf')
        
        for key_status in self.keys:
            if key_status.active:
                wait = key_status.cooldown_until - current_time
                if wait < min_wait:
                    min_wait = wait
        
        if min_wait == float('inf') or min_wait <= 0:
            return None
        
        return datetime.fromtimestamp(current_time + min_wait)
    
    def reset_all(self):
        """Reset all keys (remove cooldowns)"""
        for key_status in self.keys:
            key_status.cooldown_until = 0
            key_status.available = True
        print("🔄 All API keys reset")
    
    def get_status(self) -> Dict:
        """Get status of all keys"""
        current_time = time.time()
        
        available_count = 0
        keys_status = []
        
        for key_status in self.keys:
            cooldown_remaining = max(0, int(key_status.cooldown_until - current_time))
            is_available = key_status.active and cooldown_remaining == 0
            
            if is_available:
                available_count += 1
            
            keys_status.append({
                'id': key_status.id,
                'requests': key_status.requests,
                'success': key_status.success,
                'failures': key_status.failures,
                'cooldown_remaining': cooldown_remaining,
                'available': is_available,
                'active': key_status.active
            })
        
        return {
            'total_keys': len(self.keys),
            'available_keys': available_count,
            'keys': keys_status
        }


# ============================================================
# analyzer/gemini_analyzer.py - COMPLETE FIXED VERSION
# ============================================================
"""
Gemini AI Analyzer - FIXED with proper API rotation
"""

import json
import asyncio
from typing import Dict, Optional
from datetime import datetime
import google.generativeai as genai

from .api_manager import GeminiAPIManager


class GeminiAnalyzer:
    """Gemini site analysis - Fixed API rotation"""
    
    PROMPT = """You are an AI forensics expert. Analyze this website and provide a detailed intelligence report.

## Site Information:
- URL: {url}
- Title: {title}
- Detected AI Providers (from network): {providers}
- Detected Models (from network): {models}
- Page Content Preview: {page_text}

## Your Tasks:
1. **detected_arsenal**: List AI models used [{{"model": "name", "type": "openai/anthropic/google/opensource/proprietary"}}]
2. **marketing_claim**: What does the site claim? (1 sentence)
3. **actual_tech**: What technology are they actually using? (1 sentence)
4. **trust_score**: Score 0-100 based on transparency
5. **status**: "verified" / "suspicious" / "fake"
6. **category**: Chatbot, Image Generation, Writing, Code, Audio, Video, Productivity, Design, Search, Other
7. **pricing**: {{"type": "free/freemium/paid/usage", "details": "description"}}
8. **capabilities**: ["CHATBOT", "TEXT-TO-IMAGE", etc.]
9. **evidence_logs**: Max 5 findings ["> KEYWORD: description"]

Return ONLY valid JSON, no markdown."""

    PREFERRED_MODELS = [
        'gemini-1.5-flash',
        'gemini-1.5-flash-latest', 
        'gemini-2.0-flash-exp',
        'gemini-1.5-pro',
        'gemini-pro',
    ]

    def __init__(self):
        self.api_manager = GeminiAPIManager()
        self.model_name = None
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
    
    async def analyze(self, crawl_data: Dict) -> Optional[Dict]:
        """
        Analyze crawled data with PROPER API rotation
        Each call uses a different API key via round-robin
        """
        url = crawl_data.get('url', '')
        title = crawl_data.get('title', '')
        network = crawl_data.get('network', {})
        page_text = crawl_data.get('page_text', '')[:2000]
        
        providers = network.get('providers_found', [])
        models = network.get('detected_models', [])
        
        prompt = self.PROMPT.format(
            url=url, 
            title=title,
            providers=providers or "Not detected",
            models=models or "Not detected",
            page_text=page_text or "Content not available"
        )
        
        for attempt in range(self.max_retries):
            # ✅ KEY FIX: Get fresh key for each attempt
            key_status = await self.api_manager.get_available_key()
            
            if not key_status:
                # All keys in cooldown, wait for next available
                next_time = await self.api_manager.get_next_available_time()
                if next_time:
                    wait = min(10, max(1, (next_time - datetime.now()).total_seconds()))
                    await asyncio.sleep(wait)
                    continue
                return None
            
            try:
                # ✅ Configure with current key
                genai.configure(api_key=key_status.key)
                
                # Find best model (once)
                if not self.model_name:
                    self.model_name = self._find_best_model()
                
                model = genai.GenerativeModel(self.model_name)
                
                # Generate content with timeout
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
                
                # Parse response
                result = self._parse_response(response.text, crawl_data, providers, models)
                
                if result:
                    # ✅ Mark success
                    await self.api_manager.mark_success(key_status)
                    return result
                    
            except asyncio.TimeoutError:
                await self.api_manager.mark_error(key_status, 'timeout')
                
            except Exception as e:
                error_type = self._detect_error_type(e)
                await self.api_manager.mark_error(key_status, error_type)
            
            # Short wait before retry
            await asyncio.sleep(0.5)
        
        return None
    
    def _parse_response(self, text: str, crawl_data: Dict, 
                       providers: list, models: list) -> Optional[Dict]:
        """Parse Gemini JSON response"""
        try:
            # Extract JSON from markdown
            if '```json' in text:
                start = text.find('```json') + 7
                end = text.find('```', start)
                text = text[start:end].strip()
            elif '```' in text:
                start = text.find('```') + 3
                end = text.find('```', start)
                text = text[start:end].strip()
            
            # Find JSON object
            if not text.startswith('{'):
                start = text.find('{')
                end = text.rfind('}') + 1
                if start != -1 and end > start:
                    text = text[start:end]
            
            # Parse JSON
            analysis = json.loads(text)
            
            # Add metadata
            analysis['url'] = crawl_data.get('url', '')
            analysis['domain'] = crawl_data.get('domain', '')
            analysis['title'] = crawl_data.get('title', '')
            analysis['network_evidence'] = {
                'providers': providers, 
                'models': models
            }
            
            return analysis
            
        except json.JSONDecodeError:
            return None
        except Exception:
            return None
    
    def get_api_status(self) -> Dict:
        """Get current API status"""
        return self.api_manager.get_status()
    
    def reset_api_keys(self):
        """Reset all API keys"""
        self.api_manager.reset_all()