# -*- coding: utf-8 -*-
"""
WebDriver pool implementation for Playwright/DrissionPage browsers.
Playwright/DrissionPage浏览器的WebDriver池实现。

This module provides a pool of Playwright browser instances that can be reused
across multiple requests, improving performance by avoiding the overhead of
creating a new browser for each request.
此模块提供了一个Playwright浏览器实例池，可以在多个请求之间重用，
通过避免为每个请求创建新浏览器的开销来提高性能。
"""

from asyncio import Lock
from asyncio.queues import Queue, QueueEmpty

from aioscrapy.utils.tools import singleton


def _freeze_value(value):
    """
    Convert an arbitrary value into a hashable, comparable form.
    将任意值转换为可哈希、可比较的形式。

    Used to build a stable identity key for the request-level arguments
    (proxy, user_agent, ...) a driver was created with.
    用于为创建驱动程序时使用的请求级参数（代理、User-Agent等）构建稳定的标识键。
    """
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze_value(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(v) for v in value)
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


class WebDriverBase:

    async def setup(self):
        raise NotImplementedError

    async def quit(self):
        raise NotImplementedError


@singleton
class WebDriverPool:
    """
    A pool of WebDriver instances for Playwright browsers.
    Playwright浏览器的WebDriver实例池。

    This class manages a pool of browser instances that can be reused across
    multiple requests. It handles creation, retrieval, release, and cleanup
    of browser instances.
    此类管理一个可以在多个请求之间重用的浏览器实例池。它处理浏览器实例的
    创建、检索、释放和清理。

    The @singleton decorator ensures only one pool exists per process.
    @singleton装饰器确保每个进程只存在一个池。
    """

    def __init__(
            self,
            driver_cls: WebDriverBase,
            use_pool: bool = True,
            pool_size: int = 1,
            **kwargs
    ):
        """
        Initialize the WebDriverPool.
        初始化WebDriverPool。

        Args:
            use_pool: Whether to use pooling (True) or create a new browser for each request (False).
                     是否使用池化（True）或为每个请求创建新浏览器（False）。
            pool_size: Maximum number of browser instances to keep in the pool.
                      池中保留的最大浏览器实例数。
            driver_cls: The WebDriver class to instantiate.
                       要实例化的WebDriver类。
            **kwargs: Additional arguments to pass to the WebDriver constructor.
                     传递给WebDriver构造函数的其他参数。
        """
        self.use_pool = use_pool  # Whether to reuse browser instances
                                 # 是否重用浏览器实例
        self.pool_size = pool_size  # Maximum number of browsers in the pool
                                   # 池中的最大浏览器数量
        self.driver_cls = driver_cls  # WebDriver class to instantiate
                                     # 要实例化的WebDriver类
        self.kwargs = kwargs  # Arguments for WebDriver initialization
                             # WebDriver初始化的参数

        # Queue to store available browser instances
        # 存储可用浏览器实例的队列
        self.queue = Queue(maxsize=pool_size)
        # Lock to synchronize access to the pool
        # 用于同步访问池的锁
        self.lock = Lock()
        # Counter for active browser instances
        # 活动浏览器实例的计数器
        self.driver_count = 0

    # Attribute used to tag each pooled driver with the identity key of the
    # arguments it was created with.
    # 用于标记每个池化驱动程序创建时参数标识键的属性。
    _POOL_KEY_ATTR = '_webdriver_pool_key'

    @property
    def is_full(self):
        """
        Check if the pool has reached its maximum capacity.
        检查池是否已达到其最大容量。

        Returns:
            bool: True if the pool is full, False otherwise.
                 如果池已满，则为True，否则为False。
        """
        return self.driver_count >= self.pool_size

    def _make_key(self, kw):
        """
        Build the identity key for a set of request-level arguments.
        为一组请求级参数构建标识键。

        The key merges the pool's default arguments with the request-specific
        ones, exactly as the driver constructor would receive them, so two
        requests produce the same key iff they would produce identical drivers.
        该键将池的默认参数与请求特定的参数合并，与驱动程序构造函数接收的参数完全一致，
        因此两个请求产生相同键当且仅当它们会产生完全相同的驱动程序。

        Args:
            kw: Request-specific arguments.
               请求特定的参数。

        Returns:
            A hashable key uniquely identifying the merged arguments.
            唯一标识合并后参数的可哈希键。
        """
        kwargs = self.kwargs.copy()
        kwargs.update(kw)
        return _freeze_value(kwargs)

    def _take_matching(self, key):
        """
        Take an idle driver whose creation arguments match the given key.
        取出一个创建参数与给定键匹配的空闲驱动程序。

        Non-matching drivers are left in the queue (order preserved). Must be
        called while holding the pool lock.
        不匹配的驱动程序保留在队列中（顺序保持不变）。必须在持有池锁时调用。

        Args:
            key: The identity key built by _make_key().
                由_make_key()构建的标识键。

        Returns:
            A matching WebDriver instance, or None if no idle driver matches.
            匹配的WebDriver实例，如果没有空闲驱动程序匹配则为None。
        """
        matched = None
        skipped = []
        while True:
            try:
                driver = self.queue.get_nowait()
            except QueueEmpty:
                break
            if matched is None and getattr(driver, self._POOL_KEY_ATTR, None) == key:
                matched = driver
            else:
                skipped.append(driver)
        # Put the non-matching drivers back, preserving their order
        # 将不匹配的驱动程序放回队列，保持其顺序
        for driver in skipped:
            self.queue.put_nowait(driver)
        return matched

    async def create_driver(self, **kw):
        """
        Create a new WebDriver instance.
        创建一个新的WebDriver实例。

        This method instantiates a new browser with the specified arguments
        merged with the default arguments provided at pool initialization.
        此方法使用指定的参数与池初始化时提供的默认参数合并来实例化新的浏览器。

        Args:
            **args: Additional arguments to override the default WebDriver arguments.
                   用于覆盖默认WebDriver参数的其他参数。

        Returns:
            WebDriver: A new, initialized WebDriver instance.
                      一个新的、已初始化的WebDriver实例。
        """
        # Merge default arguments with request-specific arguments
        # 将默认参数与请求特定参数合并
        kwargs = self.kwargs.copy()
        kwargs.update(kw)

        # Create the driver instance
        # 创建驱动程序实例
        driver = self.driver_cls(**kwargs)

        # Initialize the browser
        # 初始化浏览器
        await driver.setup()

        # Tag the driver with the identity key of its creation arguments so
        # the pool only reuses it for requests with matching parameters
        # 用其创建参数的标识键标记驱动程序，以便池仅为参数匹配的请求重用它
        setattr(driver, self._POOL_KEY_ATTR, _freeze_value(kwargs))

        return driver

    async def get(self, **kwargs):
        """
        Get a WebDriver instance from the pool.
        从池中获取WebDriver实例。

        This method returns an idle browser only if it was created with the
        same request-level arguments (proxy, user_agent, ...); otherwise it
        creates a new one. Browsers are also recycled once their usage count
        (max_uses) is exhausted: the expired browser is closed and removed
        from the count, and a replacement is created for the request.
        此方法仅当空闲浏览器是使用相同的请求级参数（代理、User-Agent等）创建的
        时候才返回它；否则创建一个新浏览器。浏览器的使用次数（max_uses）耗尽后
        也会被回收：关闭到期的浏览器并从计数中移除，然后为该请求创建替代实例。

        Args:
            **kwargs: Additional arguments to pass to the WebDriver constructor if creating a new instance.
                     如果创建新实例，则传递给WebDriver构造函数的其他参数。

        Returns:
            WebDriver: A WebDriver instance ready for use.
                      准备使用的WebDriver实例。
        """
        # If pooling is disabled, always create a new browser
        # 如果禁用池化，始终创建新浏览器
        if not self.use_pool:
            return await self.create_driver(**kwargs)

        key = self._make_key(kwargs)

        # Synchronize access to the pool
        # 同步访问池
        async with self.lock:
            while True:
                # Try to reuse an idle driver created with the same arguments
                # 尝试重用使用相同参数创建的空闲驱动程序
                driver = self._take_matching(key)

                if driver is None and self.is_full:
                    if self.queue.empty():
                        # All drivers are checked out; wait for one to be
                        # released. release() never takes the lock, so this
                        # cannot deadlock.
                        # 所有驱动程序都被检出；等待一个被释放。
                        # release()不会获取锁，因此不会死锁。
                        driver = await self.queue.get()
                        if getattr(driver, self._POOL_KEY_ATTR, None) != key:
                            # The released driver does not match this request;
                            # destroy it and create a replacement instead.
                            # 释放的驱动程序与此请求不匹配；销毁它并创建替代实例。
                            await self._destroy(driver)
                            driver = None
                    else:
                        # Idle drivers exist but none match this request's
                        # parameters: recycle one to free capacity for a new
                        # matching driver, keeping the count within bounds.
                        # 存在空闲驱动程序但都不匹配此请求的参数：回收一个以为新的
                        # 匹配驱动程序释放容量，保持计数在范围内。
                        await self._destroy(self.queue.get_nowait())

                if driver is None:
                    # Create a replacement (or brand new) driver for this request
                    # 为此请求创建替代（或全新）驱动程序
                    driver = await self.create_driver(**kwargs)
                    self.driver_count += 1
                    return driver

                # Reused driver: enforce max_uses recycling. If the usage
                # count is exhausted, close the driver, remove it from the
                # count, and loop to create a replacement for this request.
                # 重用的驱动程序：执行max_uses回收。如果使用次数耗尽，关闭驱动程序，
                # 从计数中移除，并循环为此请求创建替代实例。
                max_uses = getattr(driver, 'max_uses', None)
                if max_uses is not None:
                    driver.max_uses = max_uses - 1
                    if driver.max_uses <= 0:
                        await self._destroy(driver)
                        continue

                return driver

    async def release(self, driver):
        """
        Release a WebDriver instance back to the pool.
        将WebDriver实例释放回池中。

        If pooling is disabled, the browser is closed. Otherwise, it's
        returned to the pool for reuse.
        如果禁用池化，则关闭浏览器。否则，它将返回到池中以供重用。

        Args:
            driver: The WebDriver instance to release.
                   要释放的WebDriver实例。
        """
        # If pooling is disabled, close the browser
        # 如果禁用池化，关闭浏览器
        if not self.use_pool:
            await driver.quit()
            return

        # Otherwise, return it to the pool
        # 否则，将其返回到池中
        await self.queue.put(driver)

    async def remove(self, driver):
        """
        Remove a WebDriver instance from the pool.
        从池中移除WebDriver实例。

        This method closes the browser and decrements the driver count.
        此方法关闭浏览器并减少驱动程序计数。

        Args:
            driver: The WebDriver instance to remove.
                   要移除的WebDriver实例。
        """
        await self._destroy(driver)

    async def _destroy(self, driver):
        """
        Close a WebDriver instance and remove it from the count.
        关闭WebDriver实例并从计数中移除。

        Args:
            driver: The WebDriver instance to destroy.
                   要销毁的WebDriver实例。
        """
        # Close the browser
        # 关闭浏览器
        await driver.quit()
        # Decrement the driver count
        # 减少驱动程序计数
        self.driver_count -= 1

    async def close(self):
        """
        Close all WebDriver instances in the pool.
        关闭池中的所有WebDriver实例。

        This method is called when the spider is closing. It closes all
        browser instances and resets the pool.
        当爬虫关闭时调用此方法。它关闭所有浏览器实例并重置池。
        """
        # Close all browsers in the pool
        # 关闭池中的所有浏览器
        while not self.queue.empty():
            driver = await self.queue.get()
            await driver.quit()
            self.driver_count -= 1
