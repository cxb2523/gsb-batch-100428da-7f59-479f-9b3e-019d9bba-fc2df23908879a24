#!/usr/bin/env python
"""
 Created by howie.hu at 08/04/2018.
"""

import asyncio
import time

from hproxy.spider.base_spider import ProxySpider
from hproxy.spider.proxy_spider import SSIPItem
from hproxy.spider.proxy_tools import request_url_by_aiohttp


class SSIPSpider(ProxySpider):
    """
    Fetch proxies from http://www.66ip.cn/areaindex_1/1.html
    """
    spider_name = '66ip'
    item = SSIPItem

    async def get_proxy(self):
        """
        Fetch proxies from http://www.66ip.cn
        :return:
        """
        start = time.time()
        ip_info_list = []
        for url in ['http://www.66ip.cn/areaindex_{}/1.html'.format(i) for i in range(1, 35)]:

            html = await request_url_by_aiohttp(url=url)
            if html:
                items_data = self.item.get_items(html=html)

                for item_data in items_data:
                    if item_data.values:
                        ip_info_list.append(item_data.values)

        stats = await self.process_proxies(ip_info_list)
        self.log_spider_finished(stats, start)


async def start():
    """
    Start spider
    :return:
    """
    await SSIPSpider.start()


if __name__ == '__main__':
    # Start
    asyncio.get_event_loop().run_until_complete(start())
