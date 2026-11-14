from datetime import datetime
from pydantic import BaseModel, Field


class CachePolicy(BaseModel):
    """Controls whether cached results are acceptable."""
    max_age_hours: int | None = None  # Return cached if younger than this
    prefer_cached: bool = False  # Use cached if available, regardless of age


class ContentMetadata(BaseModel):
    """Rich metadata about crawled content."""
    title: str | None = None
    word_count: int = 0
    has_code: bool = False
    has_tables: bool = False
    has_images: bool = False
    content_type: str | None = None  # article, docs, product, forum
    reading_time_minutes: float = 0.0
    heading_count: int = 0


class CrawlRequest(BaseModel):
    url: str
    max_depth: int = Field(default=3, ge=1, le=10)


class CrawlResponse(BaseModel):
    job_id: str
    url: str
    status: str


class QueryRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    include_markdown: bool = False
    cache_policy: CachePolicy | None = None


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    include_markdown: bool = False
    cache_policy: CachePolicy | None = None


class SourceInfo(BaseModel):
    url: str
    fetched_at: datetime


class EntityResult(BaseModel):
    entity_type: str
    entity_text: str
    confidence: float
    source: SourceInfo


class SchemaResult(BaseModel):
    schema_type: str
    schema_data: dict
    confidence: float
    source: SourceInfo


class PhantomResponse(BaseModel):
    """Standard response envelope — every API response uses this."""
    data: list[dict] | dict | list
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    freshness: datetime | None = None
    related: list[str] = Field(default_factory=list)


class PageContent(BaseModel):
    url: str
    markdown: str | None = None
    html: str | None = None
    markdown_meta: dict | None = None
