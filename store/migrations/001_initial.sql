-- 001_initial.sql
-- Phantombot v1 schema

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Crawled pages
CREATE TABLE IF NOT EXISTS crawled_pages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    content_type TEXT,
    html TEXT,
    headers JSONB,
    fetched_at TIMESTAMPTZ NOT NULL,
    rendered BOOLEAN DEFAULT FALSE,
    depth INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_crawled_pages_url ON crawled_pages(url);
CREATE INDEX IF NOT EXISTS idx_crawled_pages_domain ON crawled_pages(domain);

-- Extracted entities
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID NOT NULL REFERENCES crawled_pages(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_text TEXT NOT NULL,
    start_char INTEGER,
    end_char INTEGER,
    confidence FLOAT NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    pipeline_version TEXT NOT NULL,
    extracted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entities_page_id ON entities(page_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_text ON entities(entity_text);

-- Detected schemas
CREATE TABLE IF NOT EXISTS schemas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID NOT NULL REFERENCES crawled_pages(id) ON DELETE CASCADE,
    schema_type TEXT NOT NULL,
    schema_data JSONB NOT NULL,
    source_url TEXT NOT NULL,
    confidence FLOAT NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    pipeline_version TEXT NOT NULL,
    extracted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_schemas_page_id ON schemas(page_id);
CREATE INDEX IF NOT EXISTS idx_schemas_type ON schemas(schema_type);

-- Crawl jobs
CREATE TABLE IF NOT EXISTS crawl_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
