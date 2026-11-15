# -*- coding: utf-8 -*-
"""
Tests for WebDriverPool reuse and recycling semantics.
WebDriverPool复用与回收语义的测试。

These tests use an injectable fake driver class (no real browser needed) to
verify that:
- a driver is only reused when its creation arguments (proxy, user_agent, ...)
  match the request's arguments;
- a driver whose max_uses is exhausted is quit and replaced;
- driver_count always stays within [0, pool_size] under concurrent
  get/release;
- use_pool=False keeps the create-and-close-per-request behavior.
这些测试使用可注入的假驱动程序类（不需要真实浏览器）来验证：
- 仅当驱动程序的创建参数（代理、User-Agent等）与请求参数匹配时才重用它；
- max_uses耗尽的驱动程序会被quit并替换；
- 在并发获取/释放下，driver_count始终保持在[0, pool_size]范围内；
- use_pool=False保持每次请求创建并关闭的行为。
"""
import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _ensure_pkg(name):
    """Register a lightweight stub package so submodule imports don't run
    aioscrapy's heavy package __init__ (which needs optional deps like ujson).
    注册轻量级存根包，使子模块导入不会运行aioscrapy繁重的包__init__
    （它需要ujson等可选依赖）。"""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        mod.__path__ = []
        sys.modules[name] = mod
    return sys.modules[name]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ensure_pkg('aioscrapy')
_ensure_pkg('aioscrapy.utils')
# Stub the logger dependency of aioscrapy.utils.tools
# 存根aioscrapy.utils.tools的日志依赖
_log_mod = types.ModuleType('aioscrapy.utils.log')
_log_mod.logger = logging.getLogger('test_webdriver_pool')
sys.modules['aioscrapy.utils.log'] = _log_mod

_load_module('aioscrapy.utils.tools', REPO_ROOT / 'aioscrapy' / 'utils' / 'tools.py')
_driverpool = _load_module(
    'aioscrapy.core.downloader.handlers.webdriver.driverpool',
    REPO_ROOT / 'aioscrapy' / 'core' / 'downloader' / 'handlers' / 'webdriver' / 'driverpool.py',
)
WebDriverPool = _driverpool.WebDriverPool

# WebDriverPool is wrapped by the @singleton decorator; grab the undecorated
# class from the closure so each test gets a fully independent pool instance.
# WebDriverPool被@singleton装饰器包装；从闭包中获取未装饰的类，
# 以便每个测试获得完全独立的池实例。
_POOL_CLS = next(
    cell.cell_contents for cell in WebDriverPool.__closure__
    if isinstance(cell.cell_contents, type)
)


def make_fake_driver_cls():
    """
    Create a fresh fake driver class with its own instance tracking.
    创建一个带有自己实例跟踪的全新假驱动程序类。
    """

    class FakeDriver:
        created = []

        def __init__(self, max_uses=None, **kwargs):
            self.max_uses = max_uses
            self.kwargs = kwargs
            self.setup_done = False
            self.quit_count = 0
            FakeDriver.created.append(self)

        async def setup(self):
            self.setup_done = True

        async def quit(self):
            self.quit_count += 1

    return FakeDriver


def make_pool(driver_cls, use_pool=True, pool_size=1, **kwargs):
    return _POOL_CLS(driver_cls, use_pool=use_pool, pool_size=pool_size, **kwargs)


def test_same_params_driver_is_reused():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, pool_size=1)

        d1 = await pool.get(proxy='http://proxy-a:8080', user_agent='UA-A')
        await pool.release(d1)
        d2 = await pool.get(proxy='http://proxy-a:8080', user_agent='UA-A')

        assert d2 is d1
        assert d1.quit_count == 0
        assert pool.driver_count == 1
        await pool.close()

    asyncio.run(main())


def test_different_proxy_not_reused():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, pool_size=1)

        d1 = await pool.get(proxy='http://proxy-a:8080')
        await pool.release(d1)

        # Pool is full and the idle driver does not match: it must be
        # destroyed and replaced, not handed to this request.
        # 池已满且空闲驱动程序不匹配：必须将其销毁并替换，而不是交给此请求。
        d2 = await pool.get(proxy='http://proxy-b:8080')

        assert d2 is not d1
        assert d1.quit_count == 1
        assert d2.kwargs['proxy'] == 'http://proxy-b:8080'
        assert pool.driver_count == 1

        # And the new identity is the one reused afterwards
        # 并且之后重用的是新身份
        await pool.release(d2)
        d3 = await pool.get(proxy='http://proxy-b:8080')
        assert d3 is d2
        await pool.close()

    asyncio.run(main())


def test_different_user_agent_not_reused():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, pool_size=1)

        d1 = await pool.get(user_agent='UA-A')
        await pool.release(d1)
        d2 = await pool.get(user_agent='UA-B')

        assert d2 is not d1
        assert d1.quit_count == 1
        assert d2.kwargs['user_agent'] == 'UA-B'
        assert pool.driver_count == 1
        await pool.close()

    asyncio.run(main())


def test_matching_driver_found_among_others():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, pool_size=2)

        da = await pool.get(proxy='http://proxy-a:8080')
        db = await pool.get(proxy='http://proxy-b:8080')
        assert da is not db
        # Release in reverse order so a naive FIFO pop would hand out the
        # wrong driver.
        # 以相反顺序释放，这样简单的FIFO弹出会分发错误的驱动程序。
        await pool.release(db)
        await pool.release(da)

        d = await pool.get(proxy='http://proxy-a:8080')
        assert d is da
        assert da.quit_count == 0
        assert db.quit_count == 0
        assert pool.driver_count == 2
        await pool.close()

    asyncio.run(main())


def test_max_uses_recycling():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, pool_size=1, max_uses=2)

        # First use: fresh driver (max_uses=2)
        # 第一次使用：新驱动程序（max_uses=2）
        d1 = await pool.get()
        assert d1.max_uses == 2
        await pool.release(d1)

        # Second use: reused, counter decremented
        # 第二次使用：重用，计数器递减
        d2 = await pool.get()
        assert d2 is d1
        assert d2.max_uses == 1
        await pool.release(d2)

        # Third checkout: the old driver is expired -> quit and replaced
        # 第三次获取：旧驱动程序已到期 -> quit并替换
        d3 = await pool.get()
        assert d3 is not d1
        assert d1.quit_count == 1
        assert d3.max_uses == 2
        assert pool.driver_count == 1

        # Pool stays consistent afterwards
        # 之后池保持一致
        await pool.release(d3)
        assert pool.queue.qsize() == 1
        await pool.close()
        assert pool.driver_count == 0

    asyncio.run(main())


def test_max_uses_one_does_not_deadlock():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, pool_size=1, max_uses=1)

        d1 = await pool.get()
        await pool.release(d1)
        # The expired driver must be replaced, not looped forever
        # 到期的驱动程序必须被替换，而不是永远循环
        d2 = await asyncio.wait_for(pool.get(), timeout=5)
        assert d2 is not d1
        assert d1.quit_count == 1
        assert pool.driver_count == 1
        await pool.close()

    asyncio.run(main())


def test_concurrent_get_release_count_bounds():
    async def main():
        fake = make_fake_driver_cls()
        pool_size = 3
        pool = make_pool(fake, pool_size=pool_size, max_uses=4)
        violations = []

        def check_bounds():
            if not (0 <= pool.driver_count <= pool_size):
                violations.append(pool.driver_count)

        async def worker(i):
            for j in range(10):
                proxy = f'http://proxy-{i % 2}:8080'
                driver = await pool.get(proxy=proxy)
                check_bounds()
                assert driver.kwargs['proxy'] == proxy
                await asyncio.sleep(0)
                await pool.release(driver)
                check_bounds()

        await asyncio.gather(*(worker(i) for i in range(8)))

        assert not violations
        assert 0 <= pool.driver_count <= pool_size
        # All checked-in drivers are idle in the queue
        # 所有检入的驱动程序都在队列中空闲
        assert pool.queue.qsize() == pool.driver_count

        await pool.close()
        assert pool.driver_count == 0
        assert pool.queue.empty()

    asyncio.run(main())


def test_concurrent_blocking_when_all_checked_out():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, pool_size=1)

        d1 = await pool.get(proxy='http://proxy-a:8080')

        # This get() must block until d1 is released, then reuse it
        # 此get()必须阻塞直到d1被释放，然后重用它
        async def delayed_release():
            await asyncio.sleep(0.1)
            await pool.release(d1)

        task = asyncio.create_task(delayed_release())
        d2 = await asyncio.wait_for(pool.get(proxy='http://proxy-a:8080'), timeout=5)
        await task

        assert d2 is d1
        assert pool.driver_count == 1
        await pool.release(d2)
        await pool.close()

    asyncio.run(main())


def test_use_pool_false_creates_and_closes_each_time():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, use_pool=False, pool_size=1)

        d1 = await pool.get(proxy='http://proxy-a:8080')
        d2 = await pool.get(proxy='http://proxy-a:8080')
        assert d2 is not d1
        assert pool.driver_count == 0

        await pool.release(d1)
        await pool.release(d2)
        assert d1.quit_count == 1
        assert d2.quit_count == 1
        assert pool.driver_count == 0
        assert pool.queue.empty()

    asyncio.run(main())


def test_remove_on_error_keeps_count_consistent():
    async def main():
        fake = make_fake_driver_cls()
        pool = make_pool(fake, pool_size=2)

        d1 = await pool.get(proxy='http://proxy-a:8080')
        d2 = await pool.get(proxy='http://proxy-a:8080')
        assert pool.driver_count == 2

        # Simulate the handlers' error path
        # 模拟处理程序的错误路径
        await pool.remove(d1)
        assert d1.quit_count == 1
        assert pool.driver_count == 1

        # A new request can create a replacement without exceeding the cap
        # 新请求可以创建替代实例而不超过上限
        d3 = await pool.get(proxy='http://proxy-a:8080')
        assert d3 is not d1
        assert pool.driver_count == 2
        await pool.release(d2)
        await pool.release(d3)
        await pool.close()
        assert pool.driver_count == 0

    asyncio.run(main())
