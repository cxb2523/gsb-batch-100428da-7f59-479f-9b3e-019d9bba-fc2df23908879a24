#!/usr/bin/env python
"""
Tests for the reusable validate-and-save logic on ProxySpider
and the finish logs of the 66ip / xicidaili spiders.
"""
import asyncio
import importlib
import unittest

from unittest import mock

from hproxy.spider.base_spider import ProxySpider
import hproxy.spider.base_spider.proxy_spider as proxy_spider_module

ssip_module = importlib.import_module('hproxy.spider.proxy_spider.66ip_spider')
xcdl_module = importlib.import_module('hproxy.spider.proxy_spider.xicidaili_spider')


def run(coro):
    """Run a coroutine on a fresh event loop"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeLogger:
    """Record log calls for assertions"""

    def __init__(self):
        self.records = []

    def _record(self, level, type, message):
        self.records.append({'level': level, 'type': type, 'message': message})

    def info(self, type, message):
        self._record('info', type, message)

    def error(self, type, message):
        self._record('error', type, message)

    def exception(self, type, message):
        self._record('exception', type, message)

    def messages(self, type=None):
        return [r['message'] for r in self.records if type is None or r['type'] == type]

    def all_messages(self):
        return [r['message'] for r in self.records]


class FakeDBClient:
    """Fake DatabaseSetting client, raising for fields in fail_fields"""

    def __init__(self, fail_fields=()):
        self.fail_fields = set(fail_fields)
        self.saved = {}
        self.insert_calls = []

    async def insert(self, field, value):
        self.insert_calls.append(field)
        if field in self.fail_fields:
            raise RuntimeError('db error')
        self.saved[field] = value
        return True


class DemoSpider(ProxySpider):
    spider_name = 'demo'
    item = object


def make_spider(valid_proxies=(), db_fail_fields=(), raise_on=()):
    """
    Build a DemoSpider with faked validation and faked db client.

    :param valid_proxies: 'ip:port' strings that pass validation
    :param db_fail_fields: fields for which insert raises
    :param raise_on: ips for which validation itself raises
    :return: (spider, logger, db_client, validate_calls, fake_get_proxy_info)
    """
    spider = DemoSpider()
    logger = FakeLogger()
    spider.logger = logger
    db_client = FakeDBClient(fail_fields=db_fail_fields)
    spider.db_client = db_client
    valid_set = set(valid_proxies)
    validate_calls = []

    async def fake_get_proxy_info(ip, port, get_info=False):
        validate_calls.append('{0}:{1}'.format(ip, port))
        if ip in raise_on:
            raise RuntimeError('validation error')
        key = '{0}:{1}'.format(ip, port)
        if key in valid_set:
            return True, {'proxy': key, 'types': 1}
        return False, None

    return spider, logger, db_client, validate_calls, fake_get_proxy_info


class TestSaveProxy(unittest.TestCase):
    """save_proxy distinguishes invalid / save-failed / saved"""

    def test_save_proxy_success(self):
        spider, logger, db_client, calls, fake = make_spider(valid_proxies={'1.1.1.1:8080'})
        with mock.patch.object(proxy_spider_module, 'get_proxy_info', fake):
            result = run(spider.save_proxy(('1.1.1.1', '8080')))
        self.assertEqual(result, ProxySpider.PROXY_SAVED)
        self.assertEqual(db_client.saved, {'1.1.1.1:8080': {'proxy': '1.1.1.1:8080', 'types': 1}})
        self.assertEqual(len(logger.messages('Valid proxy')), 1)
        self.assertIn('had been saved', logger.messages('Valid proxy')[0])

    def test_save_proxy_invalid(self):
        spider, logger, db_client, calls, fake = make_spider()
        with mock.patch.object(proxy_spider_module, 'get_proxy_info', fake):
            result = run(spider.save_proxy(('2.2.2.2', '8080')))
        self.assertEqual(result, ProxySpider.PROXY_INVALID)
        self.assertEqual(db_client.insert_calls, [])
        self.assertEqual(len(logger.messages('Invalid proxy')), 1)
        for message in logger.all_messages():
            self.assertNotIn('had been saved', message)

    def test_save_proxy_insert_exception(self):
        spider, logger, db_client, calls, fake = make_spider(
            valid_proxies={'3.3.3.3:8080'}, db_fail_fields={'3.3.3.3:8080'})
        with mock.patch.object(proxy_spider_module, 'get_proxy_info', fake):
            result = run(spider.save_proxy(('3.3.3.3', '8080')))
        self.assertEqual(result, ProxySpider.PROXY_SAVE_FAILED)
        self.assertEqual(db_client.insert_calls, ['3.3.3.3:8080'])
        self.assertEqual(db_client.saved, {})
        self.assertEqual(len(logger.messages('Save proxy failed')), 1)
        for message in logger.all_messages():
            self.assertNotIn('had been saved', message)

    def test_save_proxy_validation_exception(self):
        spider, logger, db_client, calls, fake = make_spider(raise_on={'4.4.4.4'})
        with mock.patch.object(proxy_spider_module, 'get_proxy_info', fake):
            result = run(spider.save_proxy(('4.4.4.4', '8080')))
        self.assertEqual(result, ProxySpider.PROXY_INVALID)
        self.assertEqual(db_client.insert_calls, [])
        self.assertEqual(len(logger.messages('Invalid proxy')), 1)


class TestProcessProxies(unittest.TestCase):
    """process_proxies dedups within one round and returns countable stats"""

    def test_duplicate_validated_only_once(self):
        spider, logger, db_client, calls, fake = make_spider(valid_proxies={'1.1.1.1:8080'})
        ip_info_list = [('1.1.1.1', '8080'), ('1.1.1.1', '8080'), ('1.1.1.1', '8080')]
        with mock.patch.object(proxy_spider_module, 'get_proxy_info', fake):
            stats = run(spider.process_proxies(ip_info_list))
        self.assertEqual(calls, ['1.1.1.1:8080'])
        self.assertEqual(db_client.insert_calls, ['1.1.1.1:8080'])
        self.assertEqual(stats, {'total': 3, 'validated': 1, 'saved': 1, 'duplicate': 2, 'failed': 0})

    def test_mixed_stats(self):
        spider, logger, db_client, calls, fake = make_spider(
            valid_proxies={'1.1.1.1:8080', '3.3.3.3:8080', '4.4.4.4:8080'},
            db_fail_fields={'3.3.3.3:8080'})
        ip_info_list = [
            ('1.1.1.1', '8080'),  # saved
            ('2.2.2.2', '8080'),  # invalid
            ('3.3.3.3', '8080'),  # insert raises
            ('4.4.4.4', '8080'),  # saved
            ('1.1.1.1', '8080'),  # duplicate
        ]
        with mock.patch.object(proxy_spider_module, 'get_proxy_info', fake):
            stats = run(spider.process_proxies(ip_info_list))
        self.assertEqual(stats, {'total': 5, 'validated': 4, 'saved': 2, 'duplicate': 1, 'failed': 2})
        # the duplicate was not validated nor saved again
        self.assertEqual(sorted(calls), ['1.1.1.1:8080', '2.2.2.2:8080', '3.3.3.3:8080', '4.4.4.4:8080'])
        self.assertEqual(sorted(db_client.insert_calls), ['1.1.1.1:8080', '3.3.3.3:8080', '4.4.4.4:8080'])
        self.assertEqual(sorted(db_client.saved), ['1.1.1.1:8080', '4.4.4.4:8080'])

    def test_single_exception_does_not_break_round(self):
        spider, logger, db_client, calls, fake = make_spider(
            valid_proxies={'1.1.1.1:8080'}, raise_on={'5.5.5.5'})
        ip_info_list = [('5.5.5.5', '8080'), ('1.1.1.1', '8080')]
        with mock.patch.object(proxy_spider_module, 'get_proxy_info', fake):
            stats = run(spider.process_proxies(ip_info_list))
        self.assertEqual(stats, {'total': 2, 'validated': 2, 'saved': 1, 'duplicate': 0, 'failed': 1})
        self.assertEqual(db_client.saved, {'1.1.1.1:8080': {'proxy': '1.1.1.1:8080', 'types': 1}})

    def test_finish_log_matches_stats(self):
        spider, logger, db_client, calls, fake = make_spider(
            valid_proxies={'1.1.1.1:8080', '3.3.3.3:8080', '4.4.4.4:8080'},
            db_fail_fields={'3.3.3.3:8080'})
        ip_info_list = [
            ('1.1.1.1', '8080'),
            ('2.2.2.2', '8080'),
            ('3.3.3.3', '8080'),
            ('4.4.4.4', '8080'),
            ('1.1.1.1', '8080'),
        ]
        with mock.patch.object(proxy_spider_module, 'get_proxy_info', fake):
            stats = run(spider.process_proxies(ip_info_list))
        spider.log_spider_finished(stats, start=0)
        finish_messages = logger.messages('Spider finished!')
        self.assertEqual(len(finish_messages), 1)
        message = finish_messages[0]
        self.assertIn('total proxy num : 5', message)
        self.assertIn('validated proxy num ：4', message)
        self.assertIn('valid proxy num ：2', message)
        self.assertIn('duplicate proxy num ：1', message)
        self.assertIn('failed proxy num ：2', message)


class FakeItemData:
    def __init__(self, values):
        self.values = values


def make_page_item(proxies):
    """Fake item class returning the same proxies for every page"""

    class FakeItem:
        @staticmethod
        def get_items(html):
            return [FakeItemData(p) for p in proxies] + [FakeItemData(None)]

    return FakeItem


class TestSSIPSpider(unittest.TestCase):
    """66ip spider uses the shared validate-and-save logic"""

    def test_get_proxy_dedups_across_pages(self):
        async def fake_request(url, **kwargs):
            return '<html></html>'

        spider = ssip_module.SSIPSpider()
        logger = FakeLogger()
        spider.logger = logger
        db_client = FakeDBClient()
        spider.db_client = db_client
        spider.item = make_page_item([('1.1.1.1', '8080'), ('2.2.2.2', '8080')])

        validate_calls = []

        async def fake_get_proxy_info(ip, port, get_info=False):
            validate_calls.append('{0}:{1}'.format(ip, port))
            if ip == '1.1.1.1':
                return True, {'proxy': '1.1.1.1:8080', 'types': 1}
            return False, None

        with mock.patch.object(ssip_module, 'request_url_by_aiohttp', fake_request), \
                mock.patch.object(proxy_spider_module, 'get_proxy_info', fake_get_proxy_info):
            run(spider.get_proxy())

        # 34 pages * 2 proxies each, only 2 unique ones validated once
        self.assertEqual(sorted(validate_calls), ['1.1.1.1:8080', '2.2.2.2:8080'])
        self.assertEqual(db_client.insert_calls, ['1.1.1.1:8080'])
        finish_messages = logger.messages('Spider finished!')
        self.assertEqual(len(finish_messages), 1)
        message = finish_messages[0]
        self.assertIn('Crawling 66ip finished', message)
        self.assertIn('total proxy num : 68', message)
        self.assertIn('validated proxy num ：2', message)
        self.assertIn('valid proxy num ：1', message)
        self.assertIn('duplicate proxy num ：66', message)
        self.assertIn('failed proxy num ：1', message)


class TestXCDLSpider(unittest.TestCase):
    """xicidaili spider uses the shared validate-and-save logic"""

    def test_get_proxy_finish_log(self):
        async def fake_request(url, **kwargs):
            return '<html></html>'

        spider = xcdl_module.XCDLSpider()
        logger = FakeLogger()
        spider.logger = logger
        db_client = FakeDBClient(fail_fields={'3.3.3.3:8080'})
        spider.db_client = db_client
        spider.item = make_page_item([
            ('1.1.1.1', '8080'),  # saved
            ('2.2.2.2', '8080'),  # invalid
            ('3.3.3.3', '8080'),  # insert raises
            ('1.1.1.1', '8080'),  # duplicate
        ])

        async def fake_get_proxy_info(ip, port, get_info=False):
            if ip in ('1.1.1.1', '3.3.3.3'):
                return True, {'proxy': '{0}:8080'.format(ip), 'types': 1}
            return False, None

        with mock.patch.object(xcdl_module, 'request_url_by_aiohttp', fake_request), \
                mock.patch.object(proxy_spider_module, 'get_proxy_info', fake_get_proxy_info):
            run(spider.get_proxy())

        self.assertEqual(db_client.saved, {'1.1.1.1:8080': {'proxy': '1.1.1.1:8080', 'types': 1}})
        finish_messages = logger.messages('Spider finished!')
        self.assertEqual(len(finish_messages), 1)
        message = finish_messages[0]
        self.assertIn('Crawling xicidaili finished', message)
        self.assertIn('total proxy num : 4', message)
        self.assertIn('validated proxy num ：3', message)
        self.assertIn('valid proxy num ：1', message)
        self.assertIn('duplicate proxy num ：1', message)
        self.assertIn('failed proxy num ：2', message)
        # the failed save must not be logged as saved
        for msg in logger.all_messages():
            if '3.3.3.3' in msg:
                self.assertNotIn('had been saved', msg)

    def test_get_proxy_request_failed(self):
        async def fake_request(url, **kwargs):
            return None

        spider = xcdl_module.XCDLSpider()
        logger = FakeLogger()
        spider.logger = logger
        spider.db_client = FakeDBClient()

        with mock.patch.object(xcdl_module, 'request_url_by_aiohttp', fake_request):
            run(spider.get_proxy())

        self.assertEqual(len(logger.messages('Spider failed!')), 1)
        self.assertEqual(logger.messages('Spider finished!'), [])


if __name__ == '__main__':
    unittest.main()
