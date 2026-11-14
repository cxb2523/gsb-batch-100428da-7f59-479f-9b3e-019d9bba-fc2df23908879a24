-- /backend/supabase/schema.sql

-- Ana tablo
CREATE TABLE intelligence_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    trust_score INTEGER CHECK (trust_score >= 0 AND trust_score <= 100),
    status TEXT CHECK (status IN ('verified', 'suspicious', 'fake')),
    marketing_claim TEXT,
    detected_arsenal JSONB,  -- [{model: "GPT-4", type: "openai"}]
    capabilities TEXT[],     -- ['CHATBOT', 'CODE']
    pricing JSONB,           -- {type: "paid", details: "$19/mo"}
    evidence_logs TEXT[],
    category TEXT,
    last_verified TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- İndeksler (performans için)
CREATE INDEX idx_status ON intelligence_records(status);
CREATE INDEX idx_trust_score ON intelligence_records(trust_score);
CREATE INDEX idx_last_verified ON intelligence_records(last_verified);

-- RLS Politikaları (Güvenlik)
ALTER TABLE intelligence_records ENABLE ROW LEVEL SECURITY;

-- Public read (herkes okuyabilir)
CREATE POLICY "Public read access" 
    ON intelligence_records FOR SELECT 
    USING (true);

-- Sadece authenticated kullanıcılar yazabilir
CREATE POLICY "Authenticated write access" 
    ON intelligence_records FOR INSERT 
    TO authenticated 
    WITH CHECK (true);