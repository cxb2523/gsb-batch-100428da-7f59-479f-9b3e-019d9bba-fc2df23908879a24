from abc import ABC, abstractmethod


class BasePipeline(ABC):
    """Abstract base for all extraction pipelines."""

    @abstractmethod
    def extract(self, html: str, source_url: str) -> list:
        """Extract structured data from HTML. Returns a list of extracted items."""
        ...
