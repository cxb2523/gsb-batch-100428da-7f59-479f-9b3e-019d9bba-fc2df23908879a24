# test_supabase.py
import os
from dotenv import load_dotenv
import socket

load_dotenv()

url = os.getenv("SUPABASE_URL", "")
print(f"Supabase URL: {url}")

# URL'den hostname çıkar
if url:
    from urllib.parse import urlparse
    hostname = urlparse(url).hostname
    print(f"Hostname: {hostname}")
    
    try:
        ip = socket.gethostbyname(hostname)
        print(f"✅ DNS çözümlendi: {ip}")
    except socket.gaierror as e:
        print(f"❌ DNS hatası: {e}")