"""Local mock implementation of the AgentInBrowser REST API for tests."""

from .app import create_app
from .browser import MockBrowser, MockBrowserError

__all__ = ["create_app", "MockBrowser", "MockBrowserError"]
