"""异步行政区划爬虫包。"""

from .client import XzqhClient, XzqhClientError, XzqhEmptyDataError
from .crawler import XzqhCrawler
from .database import Database
from .models import (
    ApiDivisionNode,
    CrawlStats,
    Division,
    DivisionCode,
    DivisionLevel,
    FetchResult,
    TaskState,
)

__all__ = [
    "Database",
    "XzqhClient",
    "XzqhClientError",
    "XzqhEmptyDataError",
    "XzqhCrawler",
    "ApiDivisionNode",
    "CrawlStats",
    "Division",
    "DivisionCode",
    "DivisionLevel",
    "FetchResult",
    "TaskState",
]

__version__ = "0.1.0"
