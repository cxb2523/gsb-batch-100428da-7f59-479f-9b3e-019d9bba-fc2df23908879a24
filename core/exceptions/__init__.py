"""Custom exceptions for the GoogleMapsCrawler."""

from .crawler_exceptions import (
    CrawlerBaseException,
    BrowserInitializationError,
    DetailPageLoadError,
    ExtractionError,
    NavigationError,
    ReviewsTimeoutError,
)

__all__ = [
    "CrawlerBaseException",
    "BrowserInitializationError",
    "DetailPageLoadError",
    "ExtractionError",
    "NavigationError",
    "ReviewsTimeoutError",
]