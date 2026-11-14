#!/usr/bin/env python
"""
 Created by howie.hu at 07/04/2018.
"""
import asyncio
import time

from datetime import datetime

from hproxy.spider.proxy_tools import get_proxy_info
from hproxy.utils import logger as hproxy_logger


class ProxySpider():
    """
    ProxySpider
    """
    # Outcomes of validating and saving one proxy, so callers can count them
    PROXY_SAVED = 'saved'              # validated and saved into the database
    PROXY_INVALID = 'invalid'          # validation did not pass
    PROXY_SAVE_FAILED = 'save_failed'  # saving into the database raised an exception

    spider_name = ''
    item = None
    logger = hproxy_logger

    def __init__(self):
        if not getattr(self, 'spider_name', None):
            raise ValueError('Spider must have a spider_name')
        if not getattr(self, 'item', None):
            raise ValueError('Spider must have a item')
        setattr(self, 'spider_name', self.spider_name)
        setattr(self, 'item', self.item)
        setattr(self, 'logger', self.logger)
        # Resolved lazily on first use, so importing a spider does not
        # require a configured database backend
        self.db_client = None

    async def get_proxy(self):
        """It is a necessary method"""
        raise NotImplementedError

    def get_db_client(self):
        """
        Get the db client, creating it lazily on first use
        :return:
        """
        if self.db_client is None:
            from hproxy.database import DatabaseSetting
            self.db_client = DatabaseSetting()
        return self.db_client

    async def save_proxy(self, ip_info):
        """
        Validate one proxy and save it into the database.

        The result is one of the PROXY_* constants so the caller can count it:
            - PROXY_INVALID: the proxy did not pass validation
            - PROXY_SAVE_FAILED: saving into the database raised an exception
            - PROXY_SAVED: the proxy was validated and saved

        This method never raises: a single broken proxy or a broken insert
        must not interrupt the whole crawling round.

        :param ip_info: (0.0.0.0, 8080)
        :return:
        """
        ip, port = ip_info
        try:
            is_ok, info = await get_proxy_info(ip, port, get_info=True)
        except Exception as e:
            self.logger.exception(type='Invalid proxy',
                                  message="{0}: {1}:{2} is invalid, error: {3}".format(
                                      self.spider_name, ip, port, e))
            return self.PROXY_INVALID
        if not is_ok:
            self.logger.info(type='Invalid proxy',
                             message="{0}: {1}:{2} is invalid".format(self.spider_name, ip, port))
            return self.PROXY_INVALID
        # Save proxy
        try:
            await self.get_db_client().insert(field="{0}:{1}".format(ip, port), value=info)
        except Exception as e:
            self.logger.error(type='Save proxy failed',
                              message="{0}: {1}:{2} save failed, error: {3}".format(
                                  self.spider_name, ip, port, e))
            return self.PROXY_SAVE_FAILED
        self.logger.info(type='Valid proxy',
                         message="{0}: {1}:{2} had been saved".format(self.spider_name, ip, port))
        return self.PROXY_SAVED

    async def process_proxies(self, ip_info_list):
        """
        Validate and save a batch of proxies fetched in one crawling round.

        The same ip:port is validated only once per round: repeated
        occurrences are counted as duplicates instead of being validated
        and saved again. An exception raised for a single proxy does not
        interrupt the whole round.

        :param ip_info_list: [(ip, port), ...]
        :return: countable stats dict with keys:
            total     - proxies fetched in this round
            validated - unique proxies actually validated
            saved     - valid proxies saved into the database
            duplicate - repeated ip:port skipped in this round
            failed    - proxies invalid or failed to be saved
        """
        stats = {'total': len(ip_info_list), 'validated': 0, 'saved': 0, 'duplicate': 0, 'failed': 0}
        seen = set()
        tasks = []
        for ip_info in ip_info_list:
            key = "{0}:{1}".format(ip_info[0], ip_info[1])
            if key in seen:
                stats['duplicate'] += 1
                continue
            seen.add(key)
            tasks.append(asyncio.ensure_future(self.save_proxy(ip_info)))

        if tasks:
            done_list, pending_list = await asyncio.wait(tasks)
            for task in done_list:
                stats['validated'] += 1
                try:
                    result = task.result()
                except Exception:
                    # A single broken proxy must not interrupt the whole round
                    result = None
                if result == self.PROXY_SAVED:
                    stats['saved'] += 1
                else:
                    stats['failed'] += 1
        return stats

    def log_spider_finished(self, stats, start):
        """
        Log the finish message of one crawling round with countable stats
        :param stats: stats dict returned by process_proxies
        :param start: time.time() when the round started
        :return:
        """
        self.logger.info(type="Spider finished!",
                         message="Crawling {0} finished,total proxy num : {1} - validated proxy num ：{2} - valid proxy num ：{3} - duplicate proxy num ：{4} - failed proxy num ：{5}，Time costs ：{6}".format(
                             self.spider_name,
                             stats['total'],
                             stats['validated'],
                             stats['saved'],
                             stats['duplicate'],
                             stats['failed'],
                             time.time() - start))

    @classmethod
    async def start(cls):
        """Start a spider"""
        spider_instance = cls()
        spider_instance.logger.info(type='Spider started', message=spider_instance.spider_name + " Crawling...")
        start = datetime.now()
        # asyncio.get_event_loop().run_until_complete(spider_instance.get_proxy())
        await spider_instance.get_proxy()
        spider_instance.logger.info(type='Spider finished',
                                    message='Time usage：{seconds}'.format(seconds=(datetime.now() - start)))
