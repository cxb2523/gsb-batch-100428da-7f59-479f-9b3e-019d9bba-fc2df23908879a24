"""Integration tests for the MCP server -- verifies all tools and resources are registered."""

import pytest

from mcp_server.server import mcp


class TestServerRegistration:
    """Verify all tools and resources are registered on the FastMCP instance."""

    def test_server_name(self):
        assert mcp.name == "Phantombot"

    def test_crawl_url_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "crawl_url" in tools

    def test_query_knowledge_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "query_knowledge" in tools

    def test_search_semantic_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "search_semantic" in tools

    def test_get_entity_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "get_entity" in tools

    def test_get_page_markdown_tool_registered(self):
        tools = mcp._tool_manager._tools
        assert "get_page_markdown" in tools

    def test_all_five_tools_registered(self):
        tools = mcp._tool_manager._tools
        expected = {"crawl_url", "query_knowledge", "search_semantic", "get_entity", "get_page_markdown"}
        assert expected.issubset(set(tools.keys()))

    def test_stats_resource_registered(self):
        resources = mcp._resource_manager._resources
        assert "phantombot://stats" in resources

    def test_recent_resource_registered(self):
        resources = mcp._resource_manager._resources
        assert "phantombot://recent" in resources

    def test_both_resources_registered(self):
        resources = mcp._resource_manager._resources
        expected = {"phantombot://stats", "phantombot://recent"}
        assert expected.issubset(set(resources.keys()))
