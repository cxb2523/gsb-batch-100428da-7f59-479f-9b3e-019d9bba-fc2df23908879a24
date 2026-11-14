"""Data models for the GoogleMapsCrawler."""

from .company import CompanyData, ReviewData
from .crawl_outcome import CrawlResults, ResultOutcome, ResultStatus
from .crawler_config import CrawlerConfig, ViewPort
from .email_data import EmailData

__all__ = [
    "CompanyData",
    "CrawlResults",
    "CrawlerConfig",
    "EmailData",
    "ResultOutcome",
    "ResultStatus",
    "ReviewData",
    "ViewPort",
]
