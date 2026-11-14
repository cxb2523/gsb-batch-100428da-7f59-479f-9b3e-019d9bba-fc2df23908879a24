#!/usr/bin/env python

"""
Data models for crawl4weibo
"""

from crawl4weibo.models.batch import BatchResult, PageFailure, PageFailureReason
from crawl4weibo.models.comment import Comment
from crawl4weibo.models.post import Post
from crawl4weibo.models.user import User

__all__ = [
    "BatchResult",
    "Comment",
    "PageFailure",
    "PageFailureReason",
    "Post",
    "User",
]
