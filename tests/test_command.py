# -*- coding: utf-8 -*-
"""Tests for the project ``crawl`` command exit code."""

from __future__ import annotations

import argparse

import pytest
from scrapy.exceptions import UsageError

from olx_scraper.commands.crawl import Command


class FakeStats:
    def __init__(self, status=None):
        self.status = status

    def get_value(self, key, default=None):
        if key == "olx/crawl/status":
            return self.status
        return default


class FakeCrawler:
    def __init__(self, status=None):
        self.stats = FakeStats(status)


class FakeCrawlerProcess:
    def __init__(self, crawler):
        self.crawler = crawler
        self.bootstrap_failed = False
        self.started = False

    def create_crawler(self, name):
        return self.crawler

    def crawl(self, crawler, **kwargs):
        return None

    def start(self):
        self.started = True


def _run(command, args=("scrape_olx",)):
    command.run(list(args), argparse.Namespace(spargs={}))


def test_successful_crawl_exits_zero():
    command = Command()
    command.crawler_process = FakeCrawlerProcess(FakeCrawler("success"))
    _run(command)
    assert command.exitcode == 0


def test_failed_crawl_exits_non_zero():
    command = Command()
    command.crawler_process = FakeCrawlerProcess(FakeCrawler("failed"))
    _run(command)
    assert command.exitcode == 1


def test_missing_spider_name_is_a_usage_error():
    command = Command()
    command.crawler_process = FakeCrawlerProcess(FakeCrawler())
    with pytest.raises(UsageError):
        _run(command, args=())
