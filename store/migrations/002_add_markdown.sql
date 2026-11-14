-- 002_add_markdown.sql
-- Add markdown storage to crawled_pages

ALTER TABLE crawled_pages ADD COLUMN IF NOT EXISTS markdown TEXT;
ALTER TABLE crawled_pages ADD COLUMN IF NOT EXISTS markdown_meta JSONB;

CREATE INDEX IF NOT EXISTS idx_crawled_pages_has_markdown
    ON crawled_pages ((markdown IS NOT NULL));

COMMENT ON COLUMN crawled_pages.markdown IS
    'Clean markdown extracted from HTML, suitable for LLM consumption';
COMMENT ON COLUMN crawled_pages.markdown_meta IS
    'Metadata about the markdown extraction: word_count, has_code, has_tables, pipeline_version, extracted_at';
