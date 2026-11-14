# -*- coding: utf-8 -*-
# backend/utils/config.py
"""Configuration"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """App configuration"""
    
    # Supabase - FIX: Use SUPABASE_SERVICE_ROLE_KEY
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    
    # Gemini - Ana key
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    
    # Gemini - Ek keyler (fallback için)
    GEMINI_API_KEYS = []
    
    # Browser
    HEADLESS = os.getenv('HEADLESS', 'true').lower() == 'true'
    
    def __init__(self):
        # Tüm Gemini keylerini topla
        if self.GEMINI_API_KEY:
            self.GEMINI_API_KEYS.append(self.GEMINI_API_KEY)
        
        for i in range(1, 11):
            key = os.getenv(f'GEMINI_API_KEY_{i}')
            if key and key not in self.GEMINI_API_KEYS:
                self.GEMINI_API_KEYS.append(key)


_config = None

def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config