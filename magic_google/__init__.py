"""
    Created by howie.hu at 2021-12-18.
    Description: Core entry
    Changelog: all notable changes to this file will be documented
"""
from .search import (
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_NETWORK_ERROR,
    STATUS_PAGE_LIMIT,
    MagicGoogle,
)

__all__ = [
    "MagicGoogle",
    "STATUS_BLOCKED",
    "STATUS_COMPLETED",
    "STATUS_NETWORK_ERROR",
    "STATUS_PAGE_LIMIT",
]
