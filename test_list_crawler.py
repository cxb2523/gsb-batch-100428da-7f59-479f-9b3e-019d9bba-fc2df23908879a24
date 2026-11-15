#coding=utf8
# list_crawler 关注者列表抓取链路的本地测试
# 不依赖 redis / mongodb / 代理池 / 网络：所有外部依赖在导入 list_crawler 前替换为假实现。
# 兼容 Python 2（仓库运行环境）与 Python 3（便于本地验证）。
from __future__ import absolute_import
import sys
import types
import json
import re
import unittest


# ---------------------------------------------------------------------------
# 假实现：抓取器 / BeautifulSoup / 代理池 / 进程池 / redis
# ---------------------------------------------------------------------------

TOKEN = 'target-user'


def make_html(ids):
	'''构造含有 data-state 的假页面，字段与真实解析逻辑一致'''
	data = {'people': {'followersByUser': {TOKEN: {'ids': ids}}}}
	return '<html><div id="data" data-state=\'%s\'></div></html>' % json.dumps(data)


class FakeBeautifulSoup(object):
	'''只支持真实代码用到的 find('div', attrs={'id':'data'})['data-state']'''
	def __init__(self, html, parser=None):
		self._html = html

	def find(self, tag, attrs=None):
		m = re.search(r"data-state='([^']*)'", self._html)
		if m is None:
			raise AttributeError('data-state not found')
		return {'data-state': m.group(1)}


class FakeCrawler(object):
	'''可控的假抓取器：ok 正常返回；fail 永远抛异常；flaky 第一次抛异常后恢复'''
	def __init__(self, behavior='ok', ids=None):
		self.behavior = behavior
		self.ids = ['user-a', 'user-b'] if ids is None else ids
		self.calls = 0

	def set_proxyip(self, ip):
		pass

	def get_html(self, url):
		self.calls += 1
		if self.behavior == 'fail':
			raise Exception('fake network error')
		if self.behavior == 'flaky' and self.calls < 2:
			raise Exception('fake transient error')
		return make_html(self.ids)


class FakeProxyIP(object):
	def __init__(self):
		pass

	def get(self, protocol=-1):
		return ('127.0.0.1', 8080)


class FakeAsyncResult(object):
	def __init__(self, value):
		self._value = value

	def get(self):
		return self._value


class FakePool(object):
	'''同步执行的假进程池，避免测试依赖多进程'''
	instances = []

	def __init__(self, processes=None):
		FakePool.instances.append(self)

	def apply_async(self, func, args=()):
		return FakeAsyncResult(func(*args))

	def close(self):
		pass

	def join(self):
		pass


class FakeRedis(object):
	'''用内存 set 模拟 redis 集合操作'''
	def __init__(self):
		self.sets = {}

	def scard(self, key):
		return len(self.sets.get(key, set()))

	def spop(self, key):
		return self.sets[key].pop()

	def sadd(self, key, *values):
		self.sets.setdefault(key, set()).update(values)

	def sismember(self, key, value):
		return value in self.sets.get(key, set())


def _make_fake_common():
	'''common.py 的替身：常量与仓库保持一致，printx 兼容 Py2/Py3'''
	m = types.ModuleType('common')
	m.wait_time = 5
	m.socket_timeout = 15
	m.try_limit = 10
	m.per_page = 20
	m.list_max_process_num = 10
	m.info_max_process_num = 50
	m.waiting_set = 'waiting'
	m.info_success_set = 'info_success'
	m.info_failed_set = 'info_failed'
	m.list_success_set = 'list_success'
	m.list_failed_set = 'list_failed'
	m.host = 'https://www.zhihu.com'

	def printx(s):
		if isinstance(s, bytes):
			s = s.decode('utf8')
		sys.stdout.write(s + '\n')
	m.printx = printx
	return m


def _install_stubs():
	'''在导入 list_crawler 前，把第三方依赖与网络相关模块替换为假实现'''
	for name in ('redis', 'pymongo', 'requests', 'cookielib'):
		sys.modules[name] = types.ModuleType(name)
	bs4 = types.ModuleType('bs4')
	bs4.BeautifulSoup = FakeBeautifulSoup
	sys.modules['bs4'] = bs4
	crawler_mod = types.ModuleType('Crawler')
	crawler_mod.Crawler = FakeCrawler
	sys.modules['Crawler'] = crawler_mod
	proxyip_mod = types.ModuleType('ProxyIP')
	proxyip_mod.ProxyIP = FakeProxyIP
	sys.modules['ProxyIP'] = proxyip_mod
	if 'common' not in sys.modules:
		try:
			__import__('common')
		except Exception:
			sys.modules['common'] = _make_fake_common()


_install_stubs()
import list_crawler


def utf8(s):
	return s.encode('utf8')


# ---------------------------------------------------------------------------
# get_per_followerList：页级结局（页码 + 状态）
# ---------------------------------------------------------------------------

class PerFollowerListTest(unittest.TestCase):
	def setUp(self):
		self._old_try_limit = list_crawler.try_limit
		list_crawler.try_limit = 3

	def tearDown(self):
		list_crawler.try_limit = self._old_try_limit

	def test_success_page(self):
		result = list_crawler.get_per_followerList(TOKEN, 1, 5, crawler_factory=lambda: FakeCrawler('ok'))
		self.assertEqual(result['page'], 1)
		self.assertTrue(result['success'])
		self.assertEqual(result['followers'], [utf8('user-a'), utf8('user-b')])

	def test_failed_page_after_retries_exhausted(self):
		result = list_crawler.get_per_followerList(TOKEN, 2, 5, crawler_factory=lambda: FakeCrawler('fail'))
		self.assertEqual(result['page'], 2)
		self.assertFalse(result['success'])
		# 失败页不得携带任何用户，避免混入成功结果
		self.assertEqual(result['followers'], [])

	def test_retry_then_success(self):
		shared = FakeCrawler('flaky')
		result = list_crawler.get_per_followerList(TOKEN, 3, 5, crawler_factory=lambda: shared)
		self.assertEqual(result['page'], 3)
		self.assertTrue(result['success'])
		self.assertEqual(result['followers'], [utf8('user-a'), utf8('user-b')])
		self.assertEqual(shared.calls, 2)


# ---------------------------------------------------------------------------
# get_followerList：汇总成功页、报告失败页
# ---------------------------------------------------------------------------

def make_fake_per_page(failed_pages):
	def fake_per_page(urlToken, page, sum_page):
		if page in failed_pages:
			return {'page': page, 'success': False, 'followers': []}
		return {'page': page, 'success': True, 'followers': [utf8('user-p%d' % page)]}
	return fake_per_page


class FollowerListTest(unittest.TestCase):
	def setUp(self):
		self._old_pool = list_crawler.Pool
		self._old_per_page_func = list_crawler.get_per_followerList
		list_crawler.Pool = FakePool
		FakePool.instances = []

	def tearDown(self):
		list_crawler.Pool = self._old_pool
		list_crawler.get_per_followerList = self._old_per_page_func

	def test_partial_failure(self):
		list_crawler.get_per_followerList = make_fake_per_page({2})
		# followerCount = 3 * per_page => 共 3 页
		followers, failed = list_crawler.get_followerList(TOKEN, 3 * list_crawler.per_page)
		# 失败页的用户不得混入成功结果
		self.assertEqual(followers, [utf8('user-p1'), utf8('user-p3')])
		self.assertEqual(failed, [2])

	def test_all_pages_success(self):
		list_crawler.get_per_followerList = make_fake_per_page(set())
		followers, failed = list_crawler.get_followerList(TOKEN, 2 * list_crawler.per_page)
		self.assertEqual(followers, [utf8('user-p1'), utf8('user-p2')])
		self.assertEqual(failed, [])

	def test_zero_follower_count(self):
		followers, failed = list_crawler.get_followerList(TOKEN, 0)
		self.assertEqual(followers, [])
		self.assertEqual(failed, [])
		# followerCount 为 0 时直接返回，不应创建进程池
		self.assertEqual(FakePool.instances, [])


# ---------------------------------------------------------------------------
# start：存在失败页 => list_failed；全部成功 => list_success
# ---------------------------------------------------------------------------

class StartTest(unittest.TestCase):
	def setUp(self):
		self._saved = {}
		for name in ('Init', 'Crawler', 'proxyip', 'get_Info', 'get_followerList'):
			self._saved[name] = getattr(list_crawler, name)
		self.redis = FakeRedis()
		self.redis.sadd(list_crawler.info_success_set, TOKEN)
		list_crawler.Init = lambda: (None, self.redis)
		list_crawler.Crawler = lambda **kwargs: FakeCrawler('ok')
		list_crawler.proxyip = FakeProxyIP()
		list_crawler.get_Info = lambda c, token: {'followerCount': 40}

	def tearDown(self):
		for name, value in self._saved.items():
			setattr(list_crawler, name, value)

	def test_failed_pages_send_user_to_list_failed(self):
		list_crawler.get_followerList = lambda token, count: ([utf8('new-user')], [2])
		list_crawler.start()
		# 存在失败页：用户进入 list_failed 而不是 list_success
		self.assertTrue(self.redis.sismember(list_crawler.list_failed_set, TOKEN))
		self.assertFalse(self.redis.sismember(list_crawler.list_success_set, TOKEN))
		# 成功页抓取到的用户照常补入 waiting
		self.assertTrue(self.redis.sismember(list_crawler.waiting_set, utf8('new-user')))

	def test_all_success_send_user_to_list_success(self):
		list_crawler.get_followerList = lambda token, count: ([utf8('new-user')], [])
		list_crawler.start()
		self.assertTrue(self.redis.sismember(list_crawler.list_success_set, TOKEN))
		self.assertFalse(self.redis.sismember(list_crawler.list_failed_set, TOKEN))
		self.assertTrue(self.redis.sismember(list_crawler.waiting_set, utf8('new-user')))


if __name__ == '__main__':
	unittest.main()
