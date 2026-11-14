"""Supabase Database Client - Mevcut şemaya uyumlu"""

from typing import Dict, List, Optional
from datetime import datetime
from supabase import create_client, Client
from utils.config import get_config
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

config = get_config()


class SupabaseClient:
    """
    Supabase veritabanı işlemleri
    
    Mevcut tablo: intelligence_records
    """
    
    def __init__(self):
        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required in .env!")
        
        
        options = ClientOptions(
            auto_refresh_token=True,
            persist_session=False
        )

        self.client: Client = create_client(
            config.SUPABASE_URL,
            config.SUPABASE_SERVICE_ROLE_KEY,
            options=options
        )
        print("✅ Supabase client ready")
    
    def insert_record(self, record: Dict) -> Optional[Dict]:
        """Yeni kayıt ekle"""
        data = self._prepare_record(record)
        
        try:
            response = self.client.table('intelligence_records').insert(data).execute()
            
            if response.data:
                print(f"✅ Inserted: {data.get('name', 'unknown')[:30]}")
                return response.data[0]
            return None
                
        except Exception as e:
            error_msg = str(e).lower()
            
            # Duplicate - güncelle
            if 'duplicate' in error_msg or '23505' in error_msg:
                print(f"⚠️ Exists, updating: {data.get('url', '')[:30]}")
                return self.update_by_url(data['url'], data)
            else:
                print(f"❌ Insert error: {e}")
                return None
    
    def update_by_url(self, url: str, updates: Dict) -> Optional[Dict]:
        """URL'ye göre güncelle"""
        try:
            data = self._prepare_record(updates)
            data['updated_at'] = datetime.now().isoformat()
            
            response = self.client.table('intelligence_records').update(data).eq('url', url).execute()
            
            if response.data:
                print(f"✅ Updated: {url[:30]}")
                return response.data[0]
            return None
            
        except Exception as e:
            print(f"❌ Update error: {e}")
            return None
    
    def upsert_record(self, record: Dict) -> Optional[Dict]:
        """Insert veya Update"""
        data = self._prepare_record(record)
        
        try:
            response = self.client.table('intelligence_records').upsert(
                data,
                on_conflict='url'
            ).execute()
            
            if response.data:
                print(f"✅ Upserted: {data.get('name', 'unknown')[:30]}")
                return response.data[0]
            return None
            
        except Exception as e:
            print(f"❌ Upsert error: {e}")
            return None
    
    def batch_insert(self, records: List[Dict]) -> int:
        """Toplu ekleme"""
        success = 0
        
        for record in records:
            result = self.upsert_record(record)
            if result:
                success += 1
        
        print(f"📊 Batch: {success}/{len(records)} saved")
        return success
    
    def get_all(self, limit: int = 1000) -> List[Dict]:
        """Tüm kayıtları getir"""
        try:
            response = self.client.table('intelligence_records').select('*').order('created_at', desc=True).limit(limit).execute()
            return response.data or []
        except Exception as e:
            print(f"❌ Fetch error: {e}")
            return []
    
    def get_by_status(self, status: str) -> List[Dict]:
        """Statüye göre getir"""
        try:
            response = self.client.table('intelligence_records').select('*').eq('status', status).execute()
            return response.data or []
        except Exception as e:
            print(f"❌ Fetch error: {e}")
            return []
    
    def get_stats(self) -> Dict:
        """İstatistikler"""
        try:
            records = self.get_all()
            
            stats = {
                'total': len(records),
                'verified': sum(1 for r in records if r.get('status') == 'verified'),
                'suspicious': sum(1 for r in records if r.get('status') == 'suspicious'),
                'fake': sum(1 for r in records if r.get('status') == 'fake'),
            }
            
            scores = [r.get('trust_score', 0) for r in records if r.get('trust_score')]
            stats['avg_trust_score'] = round(sum(scores) / len(scores), 1) if scores else 0
            
            return stats
            
        except Exception as e:
            print(f"❌ Stats error: {e}")
            return {'total': 0}
    
    def check_exists(self, url: str) -> bool:
        """URL var mı?"""
        try:
            response = self.client.table('intelligence_records').select('id').eq('url', url).execute()
            return len(response.data) > 0
        except:
            return False
    def _prepare_record(self, record: Dict) -> Dict:
        """Veriyi Supabase formatına çevir"""
        
        url = record.get('url', '')
        
        # URL temizleme - çift https:// sorununu önle
        url = url.replace('https://https://', 'https://')
        url = url.replace('http://http://', 'http://')
        url = url.replace('https://http://', 'https://')
        url = url.replace('http://https://', 'https://')
        
        # Eğer protocol yoksa ekle
        if url and not url.startswith('http'):
            url = 'https://' + url
        
        # Domain çıkar
        domain = url.replace('https://', '').replace('http://', '').split('/')[0]
        
        title = record.get('title', record.get('name', ''))
        if not title:
            title = domain
        
        # Status kontrolü
        status = record.get('status', 'suspicious')
        if status not in ['verified', 'suspicious', 'fake']:
            status = 'suspicious'
        
        # Trust score kontrolü
        trust_score = record.get('trust_score', 50)
        if not isinstance(trust_score, int):
            try:
                trust_score = int(trust_score)
            except:
                trust_score = 50
        trust_score = max(0, min(100, trust_score))
        
        # Lists
        capabilities = record.get('capabilities', [])
        if not isinstance(capabilities, list):
            capabilities = []
        
        evidence_logs = record.get('evidence_logs', [])
        if not isinstance(evidence_logs, list):
            evidence_logs = []
        
        detected_arsenal = record.get('detected_arsenal', [])
        if not isinstance(detected_arsenal, list):
            detected_arsenal = []
        
        # Network evidence'dan model ekle
        network = record.get('network_evidence', {})
        if network:
            for provider in network.get('providers', []):
                detected_arsenal.append({'model': provider, 'type': provider})
            for model in network.get('models', []):
                detected_arsenal.append({'model': model, 'type': 'detected'})
        
        # Pricing
        pricing = record.get('pricing', {})
        if not isinstance(pricing, dict):
            pricing = {'type': 'unknown', 'details': str(pricing)}
        
        return {
            'name': title[:255] if title else 'Unknown',
            'url': url,  # ← Temizlenmiş URL
            'trust_score': trust_score,
            'status': status,
            'marketing_claim': record.get('marketing_claim', '')[:1000] or 'No claim',
            'detected_arsenal': detected_arsenal,
            'capabilities': capabilities,
            'pricing': pricing,
            'evidence_logs': evidence_logs,
            'category': record.get('category', 'Other'),
            'last_verified': datetime.now().isoformat(),
        }