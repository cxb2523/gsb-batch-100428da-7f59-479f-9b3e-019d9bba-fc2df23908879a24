"""Backward-compatible discovery import path."""

from newsfetch.discover import (
    DEFAULT_MAX_REQUESTS,
    DiscoveryResult,
    NewsSiteURLExtractor,
    SiteDiscoverer,
    discover,
)

__all__ = [
    "DEFAULT_MAX_REQUESTS",
    "DiscoveryResult",
    "NewsSiteURLExtractor",
    "SiteDiscoverer",
    "discover",
]
