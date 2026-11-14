from datetime import datetime
from pydantic import BaseModel, Field


class CrawlMessage(BaseModel):
    """Parsed from a Redis Streams message — matches the Go crawler output."""
    url: str
    status_code: int
    content_type: str
    html: str
    headers: dict[str, str] = Field(default_factory=dict)
    fetched_at: datetime
    rendered: bool = False
    depth: int = 0
    job_id: str | None = None


class ExtractedEntity(BaseModel):
    entity_type: str
    entity_text: str
    start_char: int | None = None
    end_char: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    pipeline_version: str = "entity-v1"


class DetectedSchema(BaseModel):
    schema_type: str
    schema_data: dict | list
    source_url: str
    confidence: float = Field(ge=0.0, le=1.0)
    pipeline_version: str = "schema-v1"


class MarkdownResult(BaseModel):
    markdown: str
    word_count: int = 0
    has_code: bool = False
    has_tables: bool = False
    has_images: bool = False
    extraction_method: str = "trafilatura_direct"  # or "trafilatura_markdownify"
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)
    pipeline_version: str = "markdown-v1"
