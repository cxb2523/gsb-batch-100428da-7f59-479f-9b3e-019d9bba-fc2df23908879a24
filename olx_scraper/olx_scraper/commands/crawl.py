# -*- coding: utf-8 -*-
"""``scrapy crawl`` with a meaningful exit code.

Scrapy exits with ``0`` whenever a crawl simply finishes, including the case
where the spider deliberately closes itself because the list page was not
recognised.  That would make a structural failure indistinguishable from a
successful run with no results.  This command keeps the stock behaviour but
returns exit code ``1`` when the spider recorded a failed outcome (read from
the ``olx/crawl/status`` statistic).

Enable it through ``COMMANDS_MODULE = "olx_scraper.commands"`` in
``settings.py`` (already done by default).
"""

from __future__ import annotations

from scrapy.commands.crawl import Command as CrawlCommand
from scrapy.exceptions import UsageError


class Command(CrawlCommand):
    def run(self, args, opts):
        if len(args) < 1:
            raise UsageError
        if len(args) > 1:
            raise UsageError(
                "running 'scrapy crawl' with more than one spider is not supported"
            )
        spname = args[0]

        assert self.crawler_process
        crawler = self.crawler_process.create_crawler(spname)
        self.crawler_process.crawl(crawler, **opts.spargs)
        self.crawler_process.start()

        if self.crawler_process.bootstrap_failed:
            self.exitcode = 1
            return

        stats = getattr(crawler, "stats", None)
        status = stats.get_value("olx/crawl/status") if stats is not None else None
        if status == "failed":
            self.exitcode = 1
