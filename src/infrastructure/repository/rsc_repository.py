#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RSC repository for managing RSC-based content extraction.
"""

from src.domain.models.wiki_page import WikiContent
from src.infrastructure.gateway.rsc_adapter import RSCAdapter


class RSCRepository:
    """
    Repository for RSC operations, using RSCAdapter.
    """

    def __init__(self, rsc_adapter: RSCAdapter):
        """
        Initialize the RSCRepository with an RSCAdapter.

        Args:
            rsc_adapter: The RSCAdapter to use for RSC operations.
        """
        self.rsc_adapter = rsc_adapter

    async def fetch_wiki_content(self, base_url: str) -> WikiContent:
        """
        通过RSC请求获取Wiki内容。

        Args:
            base_url: Wiki的基础URL

        Returns:
            WikiContent: 导航页面列表与页面内容。
        """
        return await self.rsc_adapter.fetch_wiki_content_via_rsc(base_url)
