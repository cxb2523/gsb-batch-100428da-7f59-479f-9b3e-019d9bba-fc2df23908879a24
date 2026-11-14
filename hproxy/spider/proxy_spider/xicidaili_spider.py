#!/usr/bin/env python
"""
 Created by howie.hu at 06/04/2018.
"""

import asyncio
import time

from hproxy.spider.base_spider import ProxySpider
from hproxy.spider.proxy_spider import XCDLItem
from hproxy.spider.proxy_tools import request_url_by_aiohttp


class XCDLSpider(ProxySpider):
    """
    Fetch proxies from http://www.xicidaili.com/
    """
    spider_name = 'xicidaili'
    item = XCDLItem

    async def get_proxy(self):
        """
        Fetch proxies from http://www.xicidaili.com/
        :return:
        """
        start = time.time()
        html = await request_url_by_aiohttp(url='http://www.xicidaili.com/')
        if html:
            items_data = self.item.get_items(html=html)
            ip_info_list = []
            for item_data in items_data:
                if item_data.values:
                    ip_info_list.append(item_data.values)

            stats = await self.process_proxies(ip_info_list)
            self.log_spider_finished(stats, start)
        else:
            self.logger.info(type="Spider failed!",
                             message="Crawling {0} failed，Time costs ：{1}".format(self.spider_name,
                                                                                    time.time() - start))


async def start():
    """
    Start spider
    :return:
    """
    await XCDLSpider.start()


if __name__ == '__main__':
    # Start
    asyncio.get_event_loop().run_until_complete(start())
