"""Reusable batch-scraping layer on top of the AgentInBrowser HTTP API.

Public entry points:
    - :class:`aib_batch.client.AIBClient`      thin HTTP client for the REST API
    - :class:`aib_batch.runner.BatchRunner`    ordered, settled batch execution
    - :func:`aib_batch.report.format_report`   human readable settlement report
"""

from .client import AIBClient, BrowserAPIError
from .runner import BatchRunner, STATUS_SUCCESS, STATUS_FAILED, STATUS_SKIPPED
from .report import format_report, write_json_report

__all__ = [
    "AIBClient",
    "BrowserAPIError",
    "BatchRunner",
    "STATUS_SUCCESS",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "format_report",
    "write_json_report",
]
