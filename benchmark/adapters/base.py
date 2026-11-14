from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CrawlResult:
    """Result from crawling a single URL."""

    url: str
    status_code: int
    html: str
    markdown: str
    text: str
    metadata: dict
    elapsed_seconds: float
    success: bool
    error: str | None = None


@dataclass
class ExtractionResult:
    """Entities and schemas extracted from a page."""

    entities: list[dict] = field(default_factory=list)
    schemas: list[dict] = field(default_factory=list)


class BaseAdapter(ABC):
    """Common interface for all benchmark tools."""

    @abstractmethod
    def name(self) -> str:
        """Tool display name."""
        ...

    @abstractmethod
    def version(self) -> str:
        """Tool version string."""
        ...

    @abstractmethod
    def setup(self) -> None:
        """One-time setup (start Docker containers, load models, etc.)."""
        ...

    @abstractmethod
    def teardown(self) -> None:
        """Cleanup after benchmark run."""
        ...

    @abstractmethod
    def crawl(self, url: str, timeout: int = 30) -> CrawlResult:
        """Crawl a single URL and return structured result."""
        ...

    @abstractmethod
    def extract_entities(self, html: str, url: str) -> list[dict]:
        """Extract entities from HTML. Returns list of {"text": ..., "type": ...}."""
        ...

    @abstractmethod
    def extract_schemas(self, html: str, url: str) -> list[dict]:
        """Extract schemas (JSON-LD, OpenAPI) from HTML. Returns list of {"type": ..., "data": ...}."""
        ...
