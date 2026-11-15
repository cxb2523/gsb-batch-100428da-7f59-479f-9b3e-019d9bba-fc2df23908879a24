from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RedirectHop(BaseModel):
    url: str
    status_code: int


class UrlOutcomeType(str, Enum):
    """Final, verifiable disposition of every URL the crawler discovered."""

    SUCCESS = "success"
    REDIRECT_SUCCESS = "redirect_success"
    REDIRECT_LIMIT_EXCEEDED = "redirect_limit_exceeded"
    ROBOTS_DISALLOWED = "robots_disallowed"
    RATE_LIMITED_ABANDONED = "rate_limited_abandoned"
    CONNECTION_FAILED = "connection_failed"


class UrlOutcome(BaseModel):
    """One final outcome per discovered URL (keyed by the originally requested URL)."""

    url: str
    outcome: UrlOutcomeType
    status_code: Optional[int] = None
    final_url: Optional[str] = None
    redirect_hops: int = 0
    redirect_chain: List[RedirectHop] = Field(default_factory=list)
    detail: Optional[str] = None


class ImageItem(BaseModel):
    src: str
    alt: Optional[str] = None
    loading: Optional[str] = None
    width: Optional[str] = None
    height: Optional[str] = None


class CrawlPage(BaseModel):
    url: str
    status_code: int
    content_type: str = ""
    headers: Dict[str, str] = Field(default_factory=dict)
    html: str = ""
    title: str = ""
    meta_description: str = ""
    h1s: List[str] = Field(default_factory=list)
    headings: List[Dict[str, str]] = Field(default_factory=list)  # {"level": "h2", "text": "..."}
    canonical: Optional[str] = None
    robots_meta: Optional[str] = None
    lang: Optional[str] = None
    hreflangs: List[Dict[str, str]] = Field(default_factory=list)  # {"lang": "en", "href": "..."}
    links: List[Dict[str, Any]] = Field(default_factory=list)  # {"href": "...", "text": "...", "internal": True, "rel": "..."}
    images: List[ImageItem] = Field(default_factory=list)
    scripts: List[str] = Field(default_factory=list)
    stylesheets: List[str] = Field(default_factory=list)
    schema_types: List[str] = Field(default_factory=list)
    schema_raw: List[Dict[str, Any]] = Field(default_factory=list)
    open_graph: Dict[str, str] = Field(default_factory=dict)
    twitter_card: Dict[str, str] = Field(default_factory=dict)
    response_time_ms: float = 0.0
    body_size_bytes: int = 0
    depth: int = 0
    is_indexable: bool = True
    redirect_chain: List[RedirectHop] = Field(default_factory=list)
    redirect_hops: int = 0
    final_url: Optional[str] = None


class CrawlSummary(BaseModel):
    start_url: str
    total_crawled: int = 0
    total_internal_discovered: int = 0
    total_external_discovered: int = 0
    broken_links_count: int = 0
    crawl_duration_seconds: float = 0.0
    status_codes_distribution: Dict[str, int] = Field(default_factory=dict)
    # Exactly one final outcome per discovered URL, keyed by the originally requested URL.
    url_outcomes: List[UrlOutcome] = Field(default_factory=list)
