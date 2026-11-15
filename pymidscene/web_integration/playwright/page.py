"""
Playwright 页面适配器 - 对应 packages/web-integration/src/playwright/page.ts

将 Playwright Page 适配为 PyMidscene 的统一接口。
"""

from typing import Optional, Any, List
import base64
import asyncio
import re
import sys
import time

from playwright.async_api import Page as PlaywrightPage

from ..base import AbstractInterface
from ...core.types import UIContext, ScreenshotItem
from ...shared.types import Size
from ...shared.logger import logger


class PopupNavigationError(RuntimeError):
    """``target=_blank`` 同标签页改道失败.

    改道目标不可达时抛这个错误,而不是让调用方把"跳转前的旧文档"
    当成已经到达的目标页继续截图/定位。
    """

    def __init__(self, url: Optional[str], reason: str) -> None:
        self.url = url
        self.reason = reason
        target = url or "<unknown>"
        super().__init__(
            "Popup same-tab navigation failed: the main page did not reach "
            f"{target} ({reason}). The current document is NOT the redirect "
            "target, so screenshot/locate/query are refused instead of "
            "silently returning data from the previous page."
        )


class WebPage(AbstractInterface):
    """Playwright 页面适配器"""

    # 默认配置(对齐 JS shared/constants: 5000ms / 2000ms)
    DEFAULT_WAIT_FOR_NAVIGATION_TIMEOUT = 5000
    DEFAULT_WAIT_FOR_NETWORK_IDLE_TIMEOUT = 2000

    # 早期信号排空: 只让出事件循环(不 sleep 真实时间). 实测 300 个 tick
    # 合计约 0.5ms, 且 CDP 的 targetCreated 在 click() 返回时通常已就绪。
    POPUP_HINT_TICKS = 300
    # 收到早期信号后, 等 popup 事件本身(拿到 Page 句柄才能关 tab).
    # 实测这个事件最慢可以接近 1s(冷启动/首次点击时), 给 3s 余量。
    POPUP_EVENT_TIMEOUT_MS = 3000
    # 一轮动作里最多处理几次"连开多个 tab"的收尾
    POPUP_SETTLE_ROUNDS = 3
    # popup 先落在 about:blank, 随后脚本再设 location 的常见写法: 短轮询等真实 URL
    POPUP_URL_SETTLE_MS = 1000
    # 读 popup URL 前的 load state 等待
    POPUP_LOAD_STATE_TIMEOUT_MS = 5000
    # 改道 goto 自身超时(与"导航后等待"的 wait_for_navigation_timeout 分开:
    # 后者是元素等待, 不该被当成整页加载上限)
    POPUP_REDIRECT_TIMEOUT_MS = 15000
    # 改道失败后, 等浏览器的错误页落定的上限
    POPUP_ERROR_SETTLE_MS = 2000

    def __init__(
        self,
        page: PlaywrightPage,
        wait_for_navigation_timeout: Optional[int] = None,
        wait_for_network_idle_timeout: Optional[int] = None,
        force_same_tab_navigation: bool = True,
    ):
        """
        初始化 Playwright 页面适配器

        Args:
            page: Playwright Page 实例
            wait_for_navigation_timeout: 导航超时时间（毫秒）
            wait_for_network_idle_timeout: 网络空闲超时时间（毫秒）
            force_same_tab_navigation: 是否把 target=_blank 的新 tab 改道到当前
                tab(默认开启, 对齐 JS forceClosePopup)
        """
        self.page = page
        self.wait_for_navigation_timeout = (
            wait_for_navigation_timeout
            if wait_for_navigation_timeout is not None
            else self.DEFAULT_WAIT_FOR_NAVIGATION_TIMEOUT
        )
        self.wait_for_network_idle_timeout = (
            wait_for_network_idle_timeout
            if wait_for_network_idle_timeout is not None
            else self.DEFAULT_WAIT_FOR_NETWORK_IDLE_TIMEOUT
        )

        # 对齐 JS base-page.ts:508 `everMoved`: 鼠标是否被移动过.
        # 滚动前只有"从未移动过"才把指针挪到视口中心, 否则保留上次位置
        # (hover 某容器后滚动应滚那个容器).
        self._mouse_ever_moved = False

        # 对齐 JS forceClosePopup(base-page.ts:781-812, 默认开启):
        # target=_blank 的点击会开新 tab, agent 的截图仍是旧 tab → 后续
        # 动作全部脱靶. 关闭新 tab 并在当前 tab 打开其 URL.
        self._force_same_tab_navigation = force_same_tab_navigation
        # 改道是**异步**完成的: popup 事件比动作返回晚 30-450ms. 只注册回调
        # 而不记录状态, 紧跟着的截图/定位就会落在跳转前的旧文档上. 这里把
        # "有改道在飞"和"改道失败"都显式记下来, 让 wait_for_navigation /
        # screenshot 能等到它、并在失败时拒绝把旧页面当成目标页。
        self._redirect_tasks: set[asyncio.Task] = set()
        # 早期信号: 新 tab 正在创建的迹象 (URL / 其 CDP targetId / 时刻)
        self._popup_hint_url: Optional[str] = None
        self._popup_hint_target_id: Optional[str] = None
        # 改道失败状态: 非 None 时 screenshot/locate 会拒绝把旧页面当目标页
        self._popup_redirect_error: Optional[PopupNavigationError] = None
        self._last_popup_redirect_url: Optional[str] = None
        # 等待 popup 事件落地的 future 们(由 handler 在入口处统一唤醒,
        # 避免用固定间隔轮询 —— Windows 上 sleep(0.005) 实测约 15ms)
        self._popup_waiters: List[asyncio.Future] = []
        # 同一 target=_blank 点击可能触发多次 popup 事件(脚本 click + 原生
        # 点击都会走到), 重复改道会把已到达的新页面再导航一次。用 Page
        # 对象身份 + 目标 URL 双重去重, 并用锁串行化改道。
        self._handled_popups: list[Any] = []
        self._handled_popups_limit = 20
        self._redirect_lock = asyncio.Lock()
        # CDP target 监听(Chromium); 拿不到时退回 context request 监听
        self._cdp_session: Any = None
        self._cdp_task: Optional[asyncio.Task] = None
        self._self_target_id: Optional[str] = None
        self._cdp_ready = False
        self._early_watch_started = False
        if force_same_tab_navigation:
            self._arm_popup_detection()

        logger.debug(
            f"WebPage initialized: "
            f"nav_timeout={self.wait_for_navigation_timeout}ms, "
            f"idle_timeout={self.wait_for_network_idle_timeout}ms, "
            f"force_same_tab={force_same_tab_navigation}"
        )

    # ==================== popup 同标签页改道 ====================

    def _arm_popup_detection(self) -> None:
        """注册 popup 事件, 并装上"新 tab 正在创建"的早期信号.

        popup 事件本身比动作返回晚 30-450ms(实测). 只靠它, 紧随其后的
        截图/定位必然落在改道前的旧文档上. 因此这里额外装一个早期信号:
        Chromium 下用 CDP 的 Target.targetCreated —— 它带 openerId, 能
        精确判断"这个新 page 是本页面开的", 且对页面请求零开销; 拿不到
        CDP 时(Firefox/WebKit)退回到 context 级 request 事件。
        """
        self.page.on("popup", self._on_popup_force_same_tab)
        self._ensure_early_signal_watch()

    def _ensure_early_signal_watch(self) -> None:
        """装上早期信号监听(幂等).

        CDP 会话必须在事件循环里创建, 而 WebPage 也可能在同步上下文里被
        构造(测试/工具脚本)。这种情况下先不装, 由第一次 async 调用补上;
        真实使用总是在事件循环里, 因此第一次动作之前就已经就绪。
        """
        if self._early_watch_started or not self._force_same_tab_navigation:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._early_watch_started = True
        if not self._arm_cdp_target_watch():
            self._arm_context_request_watch()

    def _arm_cdp_target_watch(self) -> bool:
        """订阅 CDP 的新 target 事件(仅 Chromium).

        返回是否成功装上。CDP 会话的建立是异步的, 这里起一个后台 task;
        在它完成前, 早期信号暂时缺失(此时仍由 popup 事件兜底)。
        """
        context = getattr(self.page, "context", None)
        if context is None or not hasattr(context, "new_cdp_session"):
            return False
        self._cdp_task = asyncio.ensure_future(self._setup_cdp_target_watch())
        self._cdp_task.add_done_callback(self._on_cdp_task_done)
        return True

    def _on_cdp_task_done(self, task: asyncio.Task) -> None:
        # 后台 task 的异常必须在这里取走, 否则 asyncio 会打 "never retrieved"
        try:
            task.result()
            return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug(f"CDP popup watch setup failed: {exc}")
        # CDP 不可用(Firefox/WebKit 或连接异常) → 退回 context request 信号
        self._arm_context_request_watch()

    async def _setup_cdp_target_watch(self) -> None:
        context = self.page.context
        session = await context.new_cdp_session(self.page)
        info = await session.send("Target.getTargetInfo")
        self._self_target_id = (info.get("targetInfo") or {}).get("targetId")
        if not self._self_target_id:
            raise RuntimeError("CDP did not expose this page's targetId")
        self._cdp_session = session
        session.on("Target.targetCreated", self._on_cdp_target_created)
        session.on("Target.targetInfoChanged", self._on_cdp_target_info_changed)
        await session.send("Target.setDiscoverTargets", {"discover": True})
        self._cdp_ready = True
        logger.debug(
            f"CDP popup watch armed (self target {self._self_target_id})"
        )

    def _on_cdp_target_created(self, event: Any) -> None:
        """CDP: 本页面开了新 page target → 记下早期信号(同步, 必须极轻)."""
        try:
            info = event.get("targetInfo") or {}
            if info.get("type") != "page":
                return
            if info.get("openerId") != self._self_target_id:
                # 同 context 的别的 tab 开的窗口不归本页面管
                return
            self._popup_hint_target_id = info.get("targetId")
            url = info.get("url")
            if url and url != "about:blank":
                self._popup_hint_url = url
        except Exception:  # pragma: no cover - 防御
            pass

    def _on_cdp_target_info_changed(self, event: Any) -> None:
        """CDP: 跟踪新 target 的 URL(它先为空, 导航提交后才有地址)."""
        try:
            info = event.get("targetInfo") or {}
            if info.get("targetId") != self._popup_hint_target_id:
                return
            url = info.get("url")
            if url and url != "about:blank":
                self._popup_hint_url = url
        except Exception:  # pragma: no cover - 防御
            pass

    def _arm_context_request_watch(self) -> None:
        """非 Chromium 的回退信号: context 级导航请求且 frame 尚未关联 page.

        这个监听对每个请求都会回调, 开销不可忽略(请求密集的页面上实测
        会让整页加载慢一倍), 所以只在拿不到 CDP 时启用。
        """
        context = getattr(self.page, "context", None)
        if context is None:
            logger.debug(
                "Page has no context; popup early detection is disabled"
            )
            return
        try:
            context.on("request", self._on_context_request)
        except Exception as exc:  # pragma: no cover - 仅防御
            logger.warning(f"Cannot subscribe to context requests: {exc}")

    def _on_context_request(self, request: Any) -> None:
        """记录"新浏览上下文即将导航"的早期信号(同步回调, 必须极轻)."""
        try:
            if not request.is_navigation_request():
                return
        except Exception:
            return
        try:
            request.frame  # 新上下文的 frame 还没有 page, 这里会抛错
            return
        except Exception:
            pass
        self._popup_hint_url = getattr(request, "url", None)

    async def _on_popup_force_same_tab(self, popup: PlaywrightPage) -> None:
        """popup 事件处理: 关掉新 tab, 当前 tab goto 它的 URL.

        Playwright 把 async 事件回调当作独立 task 调度, 所以这个方法一
        进去就把自己登记到 ``_redirect_tasks``。``wait_for_navigation``
        因此能 await 到"改道真正完成", 而不是在 goto 刚发出时就返回。
        """
        task = asyncio.current_task()
        if task is not None:
            self._redirect_tasks.add(task)
            task.add_done_callback(self._redirect_tasks.discard)
        self._notify_popup_waiters()
        try:
            await self._force_same_tab(popup)
        except asyncio.CancelledError:
            self._record_popup_failure(None, "popup handler cancelled")
            raise
        except Exception as exc:  # 事件回调不允许向外抛
            self._record_popup_failure(None, f"handler crashed: {exc!r}")
            logger.error(
                f"force_same_tab_navigation handler failed: {exc}", exc_info=True
            )

    async def _force_same_tab(self, popup: PlaywrightPage) -> None:
        """关掉 popup, 把主页面改道到它要去的地址(失败时留下诊断)."""
        if self._is_duplicate_popup(popup):
            logger.debug("Ignoring duplicate popup event for the same Page")
            return
        self._remember_popup(popup)
        hint_url = self._take_popup_hint()
        url = await self._resolve_popup_target_url(popup, hint_url)

        if self.page.is_closed():
            await self._close_popup(popup)
            logger.warning(
                "Main page was closed while handling popup; redirect skipped"
            )
            return

        # 先把新 tab 关掉: 无论改道成败, 都不该留下一个抢走后续动作的标签页。
        await self._close_popup(popup)

        if url is None:
            self._record_popup_failure(
                hint_url,
                "popup closed before exposing a navigable URL",
            )
            return

        if url == "about:blank":
            # window.open() 后由脚本自己写入内容 / 再导航: 没有可直接改道
            # 的地址. 这里不静默 —— 明确告警说明主页面仍停在旧文档上。
            logger.warning(
                "Popup target is about:blank and never navigated; "
                "main page stays on the previous document (no redirect target)"
            )
            return

        if url.startswith("chrome-error:"):
            self._record_popup_failure(
                hint_url,
                "popup landed on a browser error page (target unreachable)",
            )
            return

        async with self._redirect_lock:
            if await self._already_at(url):
                logger.debug(f"Main page is already at {url}; redirect skipped")
                self._popup_redirect_error = None
                return
            logger.info(f"Popup opened: {url}, forcing same-tab navigation")
            try:
                await self.page.goto(
                    url,
                    wait_until="load",
                    timeout=self.POPUP_REDIRECT_TIMEOUT_MS,
                )
            except Exception as exc:
                self._record_popup_failure(url, f"page.goto failed: {exc}")
                # goto 失败时主页面会落到浏览器的错误页上。这个导航可能还
                # 在收尾, 若不等它落定, 紧接着的一次显式 navigate 会被它
                # 打断(err: "interrupted by another navigation to
                # chrome-error://...")。这里等它稳定下来再返回。
                await self._settle_error_document()
                return

            # 改道落地后, 主页面必须走完与普通导航一致的等待, 否则紧接着的
            # 截图/定位仍可能读到跳转中途的文档(正是本次要修的问题)。
            await self._wait_for_navigation_impl()
            self._last_popup_redirect_url = url
            self._popup_redirect_error = None
            logger.info(f"Popup redirect completed on main page: {url}")

    def _is_duplicate_popup(self, popup: PlaywrightPage) -> bool:
        """同一个新 tab 可能连发多次 popup 事件(脚本 click + 原生点击).

        只保留最近的若干条记录: 一个长会话里 popup 次数可能很多, 无限
        累积会泄漏; 而重复事件总是紧挨着原事件到达, 小窗口就够用。
        """
        return any(p is popup for p in self._handled_popups)

    def _remember_popup(self, popup: PlaywrightPage) -> None:
        self._handled_popups.append(popup)
        if len(self._handled_popups) > self._handled_popups_limit:
            del self._handled_popups[0]

    async def _settle_error_document(self) -> None:
        """等改道失败后的浏览器错误页落定.

        失败本身已经记录在 ``_popup_redirect_error`` 里; 这里只负责让
        "错误页导航"这个副作用收尾, 避免打断调用方接下来的显式导航。
        """
        try:
            await self.page.wait_for_load_state(
                "domcontentloaded", timeout=self.POPUP_ERROR_SETTLE_MS
            )
        except Exception as exc:
            logger.debug(f"Error-page settle wait finished: {exc}")

    async def _already_at(self, url: str) -> bool:
        """主页面是否已经停在该地址(用于压掉重复改道)."""
        try:
            current = self.page.url
        except Exception:
            return False
        return current == url and current != "about:blank"

    async def _resolve_popup_target_url(
        self, popup: PlaywrightPage, hint_url: Optional[str]
    ) -> Optional[str]:
        """等 popup 提交导航后取它的目标 URL(含不可达情形的恢复)."""
        try:
            await popup.wait_for_load_state(
                "domcontentloaded", timeout=self.POPUP_LOAD_STATE_TIMEOUT_MS
            )
        except Exception as exc:
            logger.debug(f"Popup load state wait failed: {exc}")

        url = self._safe_popup_url(popup)
        if url is None:
            return None
        if url != "about:blank" and not url.startswith("chrome-error:"):
            return url

        if url == "about:blank":
            # 常见写法: window.open('about:blank') 之后再设置 location
            url = await self._wait_popup_url_settle(popup)
            if url is None:
                return None
            if url != "about:blank" and not url.startswith("chrome-error:"):
                return url

        if url.startswith("chrome-error:"):
            # 目标不可达时 popup.url 已经变成 chrome-error://chromewebdata/,
            # 原本要去的地址只剩导航历史 / 早期信号里能查到 —— 没有它,
            # 后面就只能对着旧页面继续跑, 这正是要避免的静默失败。
            recovered = await self._recover_navigation_url(popup)
            if recovered:
                logger.warning(
                    "Popup target unreachable; recovered intended URL "
                    f"from navigation history: {recovered}"
                )
                return recovered
            if hint_url:
                logger.warning(
                    "Popup target unreachable; using the early-signal URL: "
                    f"{hint_url}"
                )
                return hint_url
        return url

    async def _wait_popup_url_settle(
        self, popup: PlaywrightPage
    ) -> Optional[str]:
        """短轮询: 等 about:blank 的 popup 被脚本导航到真实地址."""
        deadline = time.monotonic() + self.POPUP_URL_SETTLE_MS / 1000
        while time.monotonic() < deadline:
            try:
                if popup.is_closed():
                    return None
            except Exception:
                return None
            await asyncio.sleep(0.05)
            url = self._safe_popup_url(popup)
            if url and url != "about:blank":
                return url
        return self._safe_popup_url(popup)

    @staticmethod
    def _safe_popup_url(popup: PlaywrightPage) -> Optional[str]:
        try:
            return popup.url
        except Exception:
            return None

    async def _recover_navigation_url(
        self, popup: PlaywrightPage
    ) -> Optional[str]:
        """从 CDP 导航历史恢复 chrome-error 页本该去的 URL(仅 Chromium)."""
        session = None
        try:
            session = await popup.context.new_cdp_session(popup)
            history = await session.send("Page.getNavigationHistory")
            entries = history.get("entries") or []
            for entry in reversed(entries):
                candidate = entry.get("url")
                if candidate and not candidate.startswith("chrome-error:"):
                    return candidate
        except Exception as exc:
            logger.debug(f"Cannot recover popup URL via CDP: {exc}")
        finally:
            if session is not None:
                try:
                    await session.detach()
                except Exception:
                    pass
        return None

    async def _close_popup(self, popup: PlaywrightPage) -> None:
        try:
            if not popup.is_closed():
                await popup.close()
        except Exception as exc:
            logger.debug(f"Failed to close popup: {exc}")

    def _has_popup_hint(self) -> bool:
        """是否有"新 tab 正在创建"的迹象.

        注意不能只看 URL: CDP 的 targetCreated 事件里 URL 往往还是空的
        (导航尚未提交), 但它带 targetId 和 openerId, 已经足以确认信号。
        """
        return bool(self._redirect_tasks or self._popup_hint_url
                    or self._popup_hint_target_id)

    def _take_popup_hint(self) -> Optional[str]:
        url = self._popup_hint_url
        self._popup_hint_url = None
        self._popup_hint_target_id = None
        return url

    def _record_popup_failure(self, url: Optional[str], reason: str) -> None:
        """记录改道失败: 之后的截图/定位会拒绝把旧页面当成目标页."""
        error = PopupNavigationError(url, reason)
        self._popup_redirect_error = error
        # 失败必须在日志里留下可诊断的痕迹(URL + 原因), 不能静默继续
        logger.error(f"Popup same-tab navigation FAILED: {error}")

    def _raise_if_popup_redirect_failed(self) -> None:
        if self._popup_redirect_error is not None:
            raise self._popup_redirect_error

    async def _await_popup_redirect(self, timeout: Optional[float] = None) -> None:
        """等待所有在飞的改道任务收尾(任务自身已吞异常, 只记录失败状态)."""
        current = asyncio.current_task()
        tasks = [
            t for t in list(self._redirect_tasks)
            if t is not current and not t.done()
        ]
        if not tasks:
            return
        logger.debug(f"Waiting for {len(tasks)} popup redirect task(s)")
        await asyncio.wait(
            tasks,
            timeout=(
                timeout
                if timeout is not None
                else (self.POPUP_REDIRECT_TIMEOUT_MS / 1000 + 5)
            ),
        )

    async def _settle_popup_redirect(self) -> None:
        """动作收尾时把"刚刚弹出的 popup"纳入等待范围.

        popup 事件比动作返回晚 30-450ms, 所以动作结束后要主动"探一下":
        先用少量 sleep(0) tick 收早期信号(实测 300 个 tick 合计约 0.5ms,
        且信号通常在第 0 个 tick 前就已到达), 一旦确认有新 tab 正在创建,
        就等 popup 事件落地 + 改道完成。没有信号时几乎零成本。
        """
        if not self._force_same_tab_navigation:
            return
        self._ensure_early_signal_watch()
        try:
            if self.page.is_closed():
                return
        except Exception:
            return

        # 允许一轮动作里连开多个 tab: 每次收尾都重新排空信号
        for _ in range(self.POPUP_SETTLE_ROUNDS):
            if not self._has_popup_hint():
                await self._drain_popup_hint_events()
            if not self._has_popup_hint():
                return
            if not self._redirect_tasks:
                await self._await_popup_event()
            await self._await_popup_redirect()

    async def _drain_popup_hint_events(self) -> None:
        """把"已经发生但还没派发"的早期信号推到回调里.

        只让出事件循环(不 sleep 真实时间), 因此不会改变动作节奏: 实测
        300 个 tick 约 0.5ms, 有信号时通常第一个 tick 前就已经置位。
        """
        for _ in range(self.POPUP_HINT_TICKS):
            if self._has_popup_hint():
                return
            await asyncio.sleep(0)

    def _notify_popup_waiters(self) -> None:
        """popup 事件已到 → 唤醒所有在等它的收尾流程."""
        waiters, self._popup_waiters = self._popup_waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(True)

    async def _await_popup_event(self) -> None:
        """等 popup 事件把 Page 句柄交给我们(拿到才能关 tab).

        早期信号只能说明"有新 tab 在创建"; 真正关掉它、拿到它的 URL 仍要
        等 popup 事件。这里用 future 唤醒而不是固定间隔轮询, 拿到事件后
        立即继续。
        """
        hint_url = self._popup_hint_url
        if self._redirect_tasks:
            return
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self._popup_waiters.append(waiter)
        try:
            await asyncio.wait_for(
                waiter, timeout=self.POPUP_EVENT_TIMEOUT_MS / 1000
            )
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                self._popup_waiters.remove(waiter)
            except ValueError:
                pass
        if self._redirect_tasks:
            return

        if self._cdp_ready and self._popup_hint_target_id:
            # CDP 明确告诉我们"本页面开了一个新 page target", 但 popup 事件
            # 迟迟没来 → 改道无法完成. 主页面仍停在旧文档上, 必须把它记为
            # 失败(而不是告警后继续), 否则后续采集会把旧页面当目标页。
            self._record_popup_failure(
                hint_url,
                "a new page was opened by this page but its popup event never "
                f"arrived within {self.POPUP_EVENT_TIMEOUT_MS}ms",
            )
        else:
            # 无 CDP 时只能靠 context 级请求事件, 它无法区分"本页面开的
            # 窗口"和"同 context 其它 tab 开的窗口", 因此这里只告警。
            logger.warning(
                "Observed a new-browser-context navigation "
                f"({hint_url!r}) but no popup event arrived within "
                f"{self.POPUP_EVENT_TIMEOUT_MS}ms; the tab was likely opened "
                "outside this page's popup chain (e.g. by another page in the "
                "same context), so no same-tab redirect was needed."
            )
        self._popup_hint_url = None
        self._popup_hint_target_id = None

    async def navigate(self, url: str) -> None:
        """对应 JS base-page.ts `navigate`.

        改道失败后主页面停在浏览器的错误页上, 而来电的 ``goto`` 可能被那次
        残留导航打断("is interrupted by another navigation"). 显式导航是
        调用方要求"离开这里"的动作, 所以这里对这类瞬时冲突做有限重试 ——
        但重试前必须先确认前一次导航真的没把页面带到目的地, 否则会把已经
        成功的导航再打断一次。
        """
        attempt = 0
        while True:
            try:
                await self.page.goto(url)
                break
            except Exception as exc:
                if not self._is_interrupted_navigation(exc) or attempt >= 2:
                    raise
                attempt += 1
                await self._wait_for_navigation_settled()
                if self._url_matches(url):
                    logger.debug(
                        f"Navigation target {url} was reached despite the "
                        "interruption; not retrying"
                    )
                    break
                logger.debug(
                    f"Navigation to {url} was interrupted; retrying "
                    f"(attempt {attempt})"
                )
        # 显式导航到别处后, 之前那次失败的改道不再"挡"住后续采集:
        # 失败状态只在原地打转(截图/定位)时才有意义。
        self._popup_redirect_error = None
        await self.wait_for_navigation()

    async def _wait_for_navigation_settled(self) -> None:
        """等当前那次导航落定(goto 被打断后调用)."""
        try:
            await self.page.wait_for_load_state(
                "load", timeout=self.POPUP_ERROR_SETTLE_MS
            )
        except Exception as exc:
            logger.debug(f"Navigation settle wait finished: {exc}")

    def _url_matches(self, url: str) -> bool:
        try:
            return self.page.url == url
        except Exception:
            return False

    @staticmethod
    def _is_interrupted_navigation(exc: BaseException) -> bool:
        """是否为"被另一次导航打断"这类可重试的瞬时错误."""
        return "is interrupted by another navigation" in str(exc)

    async def reload(self) -> None:
        """对应 JS `reload`."""
        await self.page.reload()
        await self.wait_for_navigation()

    async def go_back(self) -> None:
        """对应 JS `goBack`."""
        await self.page.go_back()
        await self.wait_for_navigation()

    async def get_ui_context(self) -> UIContext:
        """获取当前 UI 上下文"""
        screenshot_data = await self.screenshot()
        size = await self.get_size()

        # 创建 UIContext（简化版，完整实现需要更多字段）
        class WebUIContext(UIContext):
            def __init__(self, screenshot: ScreenshotItem, size: Size):
                self.screenshot = screenshot
                self.size = size
                self._is_frozen = False

        return WebUIContext(
            screenshot=ScreenshotItem(screenshot_data),
            size=size
        )

    async def get_size(self) -> Size:
        """
        获取视口尺寸 (CSS 像素) 并附带真实 devicePixelRatio.

        对应 JS base-page.ts:316-327,与 JS 保持同样的契约:
        width/height 为 CSS 像素,dpr 用于还原图像像素 → CSS 的缩放关系.
        HiDPI 屏幕下若 dpr 未返回,所有归一化-to-像素/像素 直通路径都会
        产生系统性点击偏移,因此这里总是读取真实值.
        """
        # 用 documentElement.clientWidth/clientHeight(排除滚动条宽度),对齐 JS
        # base-page.ts:316-324 —— innerWidth/Height 会把经典滚动条的 15-17px 算进
        # 去,导致归一化坐标的参考系比 JS 宽,右/下边缘出现系统性偏移。
        self._raise_if_popup_redirect_failed()
        size_info = await self.page.evaluate(
            """
            () => ({
                width: document.documentElement.clientWidth,
                height: document.documentElement.clientHeight,
                dpr: window.devicePixelRatio || 1
            })
            """
        )
        return {
            "width": float(size_info["width"]),
            "height": float(size_info["height"]),
            "dpr": float(size_info.get("dpr") or 1)
        }

    async def screenshot(self, full_page: bool = False) -> str:
        """
        获取截图(JPEG q=90 + raw base64,与 JS base-page.ts `screenshotBase64` 对齐).

        历史:之前用 PNG 返回 —— 相同内容下 PNG 体积是 JPEG q=90 的 3-5 倍,
        直接意味着 AI 请求的 image token 成本高 3-5 倍.JS 版本用 JPEG q=90 是
        midscene 的既定选择,这里对齐.

        返回格式:仍是**纯 base64**,不带 `data:image/jpeg;base64,` 前缀.调用方
        (agent._build_messages 和 SessionRecorder)会在需要时自行加前缀,这样
        既不破坏现有 call sites,又保持跨调用的数据紧凑.

        Args:
            full_page: 是否截取完整页面

        Returns:
            JPEG 图像的 Base64 字符串(无 data: 前缀)
        """
        logger.debug(f"Taking screenshot: full_page={full_page}")

        # 截图前先等导航落定(对齐 JS base-page.ts:332)—— 否则刚跳转/点击后
        # 立刻截图可能拍到旧/空白文档。timeout=10s 也对齐 JS(base-page.ts:348),
        # 避免卡页面继承 Playwright 30s 默认而阻塞 3 倍时间。
        await self.wait_for_navigation()
        screenshot_bytes = await self.page.screenshot(
            type="jpeg",
            quality=90,
            full_page=full_page,
            timeout=10000,
        )
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

        logger.debug(f"Screenshot taken: size={len(screenshot_base64)} chars (jpeg)")
        return screenshot_base64

    async def click(self, x: float, y: float) -> None:
        """
        点击指定坐标.

        Args:
            x: X 坐标 (CSS 像素,相对文档左上角)
            y: Y 坐标 (CSS 像素,相对文档左上角)
        """
        logger.debug(f"Clicking at ({x}, {y})")

        # 对齐 JS base-page.ts:368-396: 模型输出的就是视口截图坐标,
        # 直接点击, 不做任何"视口外滚动修正"(那会把视口坐标当文档坐标用).
        await self.page.mouse.click(x, y)
        self._mouse_ever_moved = True

        # 等待可能的导航
        await self.wait_for_navigation()

    async def input_text(
        self,
        text: str,
        x: Optional[float] = None,
        y: Optional[float] = None,
        clear_first: bool = True
    ) -> None:
        """
        输入文本
        
        🔑 关键：输入前总是将元素滚动到视口中心

        Args:
            text: 要输入的文本
            x: 可选的 X 坐标（先点击再输入）
            y: 可选的 Y 坐标（先点击再输入）
            clear_first: 是否先清空输入框（默认 True，与 JS 版本一致）
        """
        logger.debug(f"Inputting text: '{text}' at ({x}, {y}), clear_first={clear_first}")

        # 如果提供了坐标,聚焦到该点(agent 层已经 scrollIntoView,这里只做视口兜底)
        if x is not None and y is not None:
            await self.page.mouse.click(x, y)
            self._mouse_ever_moved = True
            await asyncio.sleep(0.1)

        # 清空输入框内容(跨平台: mac 使用 Meta+A,其他平台 Ctrl+A —— 与 JS base-page.ts:481-504 对齐)
        if clear_first:
            is_mac = sys.platform == "darwin"
            select_all_chord = "Meta+a" if is_mac else "Control+a"
            try:
                await self.page.keyboard.press(select_all_chord)
                await asyncio.sleep(0.05)
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.debug(f"Clear with {select_all_chord} failed, falling back: {e}")
                # 备用方法: 多次退格清除
                await self.page.keyboard.press("End")
                for _ in range(100):
                    await self.page.keyboard.press("Backspace")

        # 输入文本 —— delay=80ms 对齐 JS base-page.ts:447,避免受控输入框丢字
        await self.page.keyboard.type(text, delay=80)
        # 输入可能触发自动提交/跳转,等导航落定(对齐 JS afterInvokeAction)
        await self.wait_for_navigation()

    async def double_click(self, x: float, y: float) -> None:
        """
        双击坐标。对应 JS base-page.ts `leftDoubleClick`。
        """
        logger.debug(f"Double-clicking at ({x}, {y})")
        await self.page.mouse.dblclick(x, y)
        self._mouse_ever_moved = True
        await self.wait_for_navigation()

    async def right_click(self, x: float, y: float) -> None:
        """
        右键点击坐标。对应 JS base-page.ts `rightClick`。
        """
        logger.debug(f"Right-clicking at ({x}, {y})")
        await self.page.mouse.click(x, y, button="right")
        self._mouse_ever_moved = True
        await self.wait_for_navigation()

    async def drag_and_drop(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
    ) -> None:
        """
        拖放操作。对应 JS base-page.ts `dragAndDrop`。
        Playwright 原生无单步 drag-and-drop API,用 mouse.move → down → move → up 组合。
        """
        logger.debug(f"Drag from ({from_x}, {from_y}) to ({to_x}, {to_y})")
        # 节拍对齐 JS base-page.ts:416-439: move → 200ms → down → 300ms →
        # 20 步插值 move → 500ms → up → 200ms. 拖拽排序/HTML5 DnD 等页面
        # 依赖这些事件间隔.
        await self.page.mouse.move(from_x, from_y)
        await asyncio.sleep(0.2)
        await self.page.mouse.down()
        await asyncio.sleep(0.3)
        steps = 20
        for i in range(1, steps + 1):
            ix = from_x + (to_x - from_x) * (i / steps)
            iy = from_y + (to_y - from_y) * (i / steps)
            await self.page.mouse.move(ix, iy)
        await asyncio.sleep(0.5)
        await self.page.mouse.up()
        await asyncio.sleep(0.2)
        self._mouse_ever_moved = True
        await self.wait_for_navigation()

    async def long_press(
        self, x: float, y: float, duration: int = 500
    ) -> None:
        """
        长按. 对应 JS base-page.ts:671-695 `longPress`:
        duration 夹在 [300, 600]ms, mouse move → down → 等待 → up.
        """
        duration = max(300, min(int(duration or 500), 600))
        logger.debug(f"Long-pressing at ({x}, {y}) for {duration}ms")
        await self.page.mouse.move(x, y)
        await self.page.mouse.down(button="left")
        await asyncio.sleep(duration / 1000)
        await self.page.mouse.up(button="left")
        self._mouse_ever_moved = True
        await self.wait_for_navigation()

    async def hover(self, x: float, y: float) -> None:
        """
        悬停到指定坐标.

        Args:
            x: X 坐标 (CSS 像素)
            y: Y 坐标 (CSS 像素)
        """
        logger.debug(f"Hovering at ({x}, {y})")

        await self.page.mouse.move(x, y)
        self._mouse_ever_moved = True
        await self.wait_for_navigation()

    async def scroll(
        self,
        direction: str,
        distance: Optional[int] = None,
        starting_point: Optional[dict] = None
    ) -> None:
        """
        滚动页面（与 JS 版本对齐，使用 mouse.wheel）

        JS 版本使用 mouse.wheel 而非 window.scrollBy，这是关键区别：
        - mouse.wheel 会滚动鼠标指针下方的实际可滚动容器（如 SPA 内部的 div）
        - window.scrollBy 只滚动主文档窗口，对内部滚动容器无效

        对应 JS: base-page.ts scrollDown/scrollUp/scrollLeft/scrollRight

        Args:
            direction: 滚动方向（up/down/left/right）
            distance: 滚动距离（像素），默认为视口高度的 70%（与 JS 版本一致）
            starting_point: 滚动起点坐标 {"x": ..., "y": ...}，默认为视口中心
        """
        logger.debug(f"Scrolling {direction}, distance={distance}")

        if distance is None:
            # 默认滚动视口高度的 70%（与 JS 版本 innerHeight * 0.7 对齐）
            size = await self.get_size()
            if direction in ("up", "down"):
                distance = int(size["height"] * 0.7)
            else:
                distance = int(size["width"] * 0.7)

        # 滚动前先移动鼠标到指定位置；无指定位置时只有"鼠标从未移动过"
        # 才移到视口中心（对应 JS moveToPointBeforeScroll 的 everMoved 语义：
        # hover 某容器后滚动应保留指针位置、滚动那个容器）
        await self._move_to_point_before_scroll(starting_point)

        # 根据方向计算滚动量
        delta_x = 0
        delta_y = 0

        if direction == "down":
            delta_y = distance
        elif direction == "up":
            delta_y = -distance
        elif direction == "right":
            delta_x = distance
        elif direction == "left":
            delta_x = -distance
        else:
            raise ValueError(f"Invalid scroll direction: {direction}")

        # 使用 mouse.wheel 滚动（与 JS 版本完全一致）
        await self.page.mouse.wheel(delta_x, delta_y)

        # 等待滚动完成（与 JS 版本 sleep(500) 对齐）
        await asyncio.sleep(0.5)
        # 滚动可能触发无限加载导致的跳转/重排,等导航落定(对齐 JS afterInvokeAction)
        await self.wait_for_navigation()

    async def _move_to_point_before_scroll(
        self, starting_point: dict | None
    ) -> None:
        """对应 JS base-page.ts:509-519 `moveToPointBeforeScroll`."""
        if starting_point:
            await self.page.mouse.move(starting_point["x"], starting_point["y"])
            self._mouse_ever_moved = True
        elif not self._mouse_ever_moved:
            size = await self.get_size()
            await self.page.mouse.move(
                int(size["width"] / 2), int(size["height"] / 2)
            )
            self._mouse_ever_moved = True

    @staticmethod
    def _start_point_to_dict(
        start_point: tuple[float, float] | None,
    ) -> dict | None:
        if start_point is None:
            return None
        return {"x": start_point[0], "y": start_point[1]}

    async def scroll_until_top(
        self, start_point: tuple[float, float] | None = None
    ) -> None:
        """对应 JS base-page.ts:521-524 `scrollUntilTop`: 大值 mouse.wheel.

        能滚动指针下方的内部滚动容器（SPA 列表/模态框），这是
        `window.scrollTo` 做不到的。
        """
        await self._move_to_point_before_scroll(
            self._start_point_to_dict(start_point)
        )
        await self.page.mouse.wheel(0, -9999999)

    async def scroll_until_bottom(
        self, start_point: tuple[float, float] | None = None
    ) -> None:
        """对应 JS `scrollUntilBottom`."""
        await self._move_to_point_before_scroll(
            self._start_point_to_dict(start_point)
        )
        await self.page.mouse.wheel(0, 9999999)

    async def scroll_until_left(
        self, start_point: tuple[float, float] | None = None
    ) -> None:
        """对应 JS `scrollUntilLeft`."""
        await self._move_to_point_before_scroll(
            self._start_point_to_dict(start_point)
        )
        await self.page.mouse.wheel(-9999999, 0)

    async def scroll_until_right(
        self, start_point: tuple[float, float] | None = None
    ) -> None:
        """对应 JS `scrollUntilRight`."""
        await self._move_to_point_before_scroll(
            self._start_point_to_dict(start_point)
        )
        await self.page.mouse.wheel(9999999, 0)

    # 规划器常见的键名写法 → Playwright 认可的键名
    _KEY_NAME_MAP = {
        "ctrl": "Control",
        "control": "Control",
        "cmd": "Meta",
        "command": "Meta",
        "meta": "Meta",
        "win": "Meta",
        "alt": "Alt",
        "option": "Alt",
        "shift": "Shift",
        "esc": "Escape",
        "escape": "Escape",
        "enter": "Enter",
        "return": "Enter",
        "backspace": "Backspace",
        "delete": "Delete",
        "del": "Delete",
        "tab": "Tab",
        "space": "Space",
        "spacebar": "Space",
        "up": "ArrowUp",
        "down": "ArrowDown",
        "left": "ArrowLeft",
        "right": "ArrowRight",
        "arrowup": "ArrowUp",
        "arrowdown": "ArrowDown",
        "arrowleft": "ArrowLeft",
        "arrowright": "ArrowRight",
        "pageup": "PageUp",
        "pagedown": "PageDown",
        "home": "Home",
        "end": "End",
        "insert": "Insert",
    }

    @classmethod
    def _normalize_key(cls, key: str) -> str:
        """
        对齐 JS web-page.ts `normalizeKeyInputs`/`transformHotkeyInput`:
        接受 "ctrl + a"、"meta a"、"Ctrl+A" 等规划器输出, 转成 Playwright
        认可的 "Control+a" 组合键格式; 否则 Ctrl+A 全选这类 keyName 会
        直接抛 "Unknown key".
        """
        parts = [p for p in re.split(r"[+\s]+", key.strip()) if p]
        if not parts:
            return key
        normalized = []
        for part in parts:
            lower = part.lower()
            if lower in cls._KEY_NAME_MAP:
                normalized.append(cls._KEY_NAME_MAP[lower])
            elif len(part) == 1:
                # 组合键中的字母小写(Playwright 习惯), 单键保持原样
                normalized.append(part.lower() if len(parts) > 1 else part)
            else:
                normalized.append(part[0].upper() + part[1:])
        return "+".join(normalized)

    async def key_press(self, key: str) -> None:
        """
        按键

        Args:
            key: 按键名称（Enter、Escape、"Ctrl+A" 等组合键）
        """
        normalized = self._normalize_key(key)
        logger.debug(f"Pressing key: {key} -> {normalized}")

        await self.page.keyboard.press(normalized)

        # 等待可能的导航（如按 Enter 提交表单）
        await self.wait_for_navigation()

    async def wait_for_navigation(self, timeout: Optional[int] = None) -> None:
        """
        等待导航完成

        除元素等待外, 这里还负责把"刚弹出的新 tab 改道到当前 tab"这件事
        收尾: popup 事件本身比动作返回晚 30-450ms, 若不等, 紧接着的截图
        和元素定位就会落在改道前的旧文档上。

        Args:
            timeout: 超时时间（毫秒）

        Raises:
            PopupNavigationError: 同标签页改道失败 —— 当前文档不是改道目标,
                调用方不应把它当成"已到达目标页"继续采集。
        """
        # 改道是异步发起的, 无论动作配置的导航等待是多久都要先把它等完;
        # 否则 timeout=0 的调用方会直接读到旧页面。
        await self._settle_popup_redirect()
        self._raise_if_popup_redirect_failed()

        await self._wait_for_navigation_impl(timeout)

    async def _wait_for_navigation_impl(
        self, timeout: Optional[int] = None
    ) -> None:
        timeout_ms = (
            timeout if timeout is not None else self.wait_for_navigation_timeout
        )

        if timeout_ms == 0:
            logger.debug("Navigation timeout is 0, skipping wait")
            return

        logger.debug(f"Waiting for navigation (timeout={timeout_ms}ms)")

        try:
            # 等待 HTML 元素存在（表示页面已加载）
            await self.page.wait_for_selector(
                "html",
                timeout=timeout_ms
            )
        except Exception as e:
            logger.warning(
                f"Waiting for navigation timed out, but continuing execution: {e}"
            )

    async def wait_for_network_idle(self, timeout: Optional[int] = None) -> None:
        """
        等待网络空闲

        Args:
            timeout: 超时时间（毫秒）
        """
        timeout_ms = (
            timeout if timeout is not None else self.wait_for_network_idle_timeout
        )

        if timeout_ms == 0:
            logger.debug("Network idle timeout is 0, skipping wait")
            return

        logger.debug(f"Waiting for network idle (timeout={timeout_ms}ms)")

        try:
            # Playwright 的网络空闲等待
            await self.page.wait_for_load_state(
                "networkidle",
                timeout=timeout_ms
            )
        except Exception as e:
            logger.warning(
                f"Waiting for network idle timed out, but continuing execution: {e}"
            )

    async def evaluate_javascript(self, script: str) -> Any:
        """
        执行 JavaScript 代码

        Args:
            script: JavaScript 代码

        Returns:
            执行结果
        """
        logger.debug(f"Evaluating JavaScript: {script[:100]}...")

        self._raise_if_popup_redirect_failed()
        result = await self.page.evaluate(script)

        logger.debug(f"JavaScript evaluation result: {str(result)[:100]}...")

        return result

    async def wait(self, timeout_ms: int) -> None:
        """
        等待指定时间

        Args:
            timeout_ms: 等待时间（毫秒）
        """
        logger.debug(f"Waiting for {timeout_ms}ms")
        await asyncio.sleep(timeout_ms / 1000)

    # ==================== XPath 相关方法（与 JS 版本对齐） ====================

    async def get_element_xpath(self, x: float, y: float) -> Optional[str]:
        """
        获取指定坐标处元素的 XPath

        Args:
            x: X 坐标
            y: Y 坐标

        Returns:
            元素的 XPath 路径，如果找不到则返回 None
        """
        logger.debug(f"Getting XPath for element at ({x}, {y})")

        self._raise_if_popup_redirect_failed()
        # JavaScript 代码：获取坐标处元素的 XPath
        xpath = await self.page.evaluate("""
            (coords) => {
                const { x, y } = coords;
                const element = document.elementFromPoint(x, y);
                if (!element) return null;

                // 生成 XPath 的函数
                function getXPath(el) {
                    if (!el || el.nodeType !== Node.ELEMENT_NODE) return '';
                    
                    // 如果有 ID，使用 ID 定位（更稳定）
                    // 注意：JS 版本不使用 ID，我们也保持一致
                    
                    const parts = [];
                    let current = el;
                    
                    while (current && current.nodeType === Node.ELEMENT_NODE) {
                        let index = 1;
                        let sibling = current.previousElementSibling;
                        
                        while (sibling) {
                            if (sibling.nodeName === current.nodeName) {
                                index++;
                            }
                            sibling = sibling.previousElementSibling;
                        }
                        
                        const tagName = current.nodeName.toLowerCase();
                        parts.unshift(`${tagName}[${index}]`);
                        current = current.parentElement;
                    }
                    
                    return '/' + parts.join('/');
                }

                return getXPath(element);
            }
        """, {"x": x, "y": y})

        if xpath:
            logger.debug(f"XPath found: {xpath}")
        else:
            logger.warning(f"No element found at ({x}, {y})")

        return xpath

    async def get_element_xpaths(self, x: float, y: float) -> List[str]:
        """
        Return **multiple** XPath candidates for the element at ``(x, y)``.

        M1: a single ``tag[n]`` path is brittle — one sibling insertion
        shifts all indices and cache misses. We return several fallback
        candidates in priority order so cache hits survive minor DOM drift:

        1. ``//tag[@id='...']`` if the element has an id
        2. ``//tag[@data-testid='...']`` / ``[@aria-label='...']`` etc.
        3. ``//tag[normalize-space(text())='<text>']`` when the element has
           short stable text
        4. the full ``/html/body/.../tag[n]`` tag-indexed path (legacy fallback)

        Mirrors (loosely) JS ``getXpathsByPoint`` from
        ``@midscene/shared/extractor`` which also returns a ranked list.
        """
        logger.debug(f"Getting XPath candidates for element at ({x}, {y})")

        self._raise_if_popup_redirect_failed()
        xpaths: List[str] = await self.page.evaluate(
            """
            (coords) => {
                const { x, y } = coords;
                const el = document.elementFromPoint(x, y);
                if (!el) return [];

                const results = [];

                const escAttr = (v) => String(v).replace(/'/g, "\\\\'");
                const tag = el.nodeName.toLowerCase();

                // 1. id
                if (el.id && /^[A-Za-z][\\w\\-:.]*$/.test(el.id)) {
                    results.push(`//${tag}[@id='${escAttr(el.id)}']`);
                }
                // 2. test/a11y attrs
                for (const attr of ['data-testid','data-test-id','data-test','aria-label','name','role']) {
                    const v = el.getAttribute(attr);
                    if (v && v.length <= 80) {
                        results.push(`//${tag}[@${attr}='${escAttr(v)}']`);
                    }
                }
                // 3. short stable text
                const text = (el.textContent || '').trim();
                if (text && text.length <= 40 && /\\S/.test(text)) {
                    const oneLine = text.replace(/\\s+/g, ' ');
                    results.push(`//${tag}[normalize-space(text())='${escAttr(oneLine)}']`);
                }

                // 4. full tag[n] path (always include as last-ditch fallback)
                const parts = [];
                let cur = el;
                while (cur && cur.nodeType === Node.ELEMENT_NODE) {
                    let idx = 1;
                    let sib = cur.previousElementSibling;
                    while (sib) {
                        if (sib.nodeName === cur.nodeName) idx++;
                        sib = sib.previousElementSibling;
                    }
                    parts.unshift(`${cur.nodeName.toLowerCase()}[${idx}]`);
                    cur = cur.parentElement;
                }
                results.push('/' + parts.join('/'));

                // Dedup preserving order
                const seen = new Set();
                return results.filter(p => {
                    if (seen.has(p)) return false;
                    seen.add(p);
                    return true;
                });
            }
            """,
            {"x": x, "y": y},
        )
        if xpaths:
            logger.debug(f"XPath candidates ({len(xpaths)}): {xpaths[0][:80]}...")
        return xpaths or []

    async def get_element_by_xpath(self, xpath: str) -> Optional[dict]:
        """
        通过 XPath 获取元素信息（边界框和中心点）

        Args:
            xpath: 元素的 XPath 路径

        Returns:
            包含 bbox 和 center 的字典，如果找不到则返回 None
        """
        logger.debug(f"Getting element by XPath: {xpath}")

        self._raise_if_popup_redirect_failed()
        try:
            # 使用 Playwright 的 locator 通过 XPath 定位
            locator = self.page.locator(f"xpath={xpath}")
            
            # 检查元素是否存在
            count = await locator.count()
            if count == 0:
                logger.warning(f"Element not found for XPath: {xpath}")
                return None

            # 获取第一个匹配元素的边界框
            bounding_box = await locator.first.bounding_box()
            
            if bounding_box is None:
                logger.warning(f"Element has no bounding box: {xpath}")
                return None

            # 计算中心点
            center_x = bounding_box["x"] + bounding_box["width"] / 2
            center_y = bounding_box["y"] + bounding_box["height"] / 2

            result = {
                "bbox": [
                    bounding_box["x"],
                    bounding_box["y"],
                    bounding_box["x"] + bounding_box["width"],
                    bounding_box["y"] + bounding_box["height"]
                ],
                "center": [center_x, center_y],
                "rect": {
                    "left": bounding_box["x"],
                    "top": bounding_box["y"],
                    "width": bounding_box["width"],
                    "height": bounding_box["height"]
                }
            }

            logger.debug(f"Element found: center=({center_x}, {center_y})")
            return result

        except Exception as e:
            logger.warning(f"Failed to get element by XPath: {xpath}, error: {e}")
            return None

    async def click_by_xpath(self, xpath: str) -> bool:
        """
        通过 XPath 点击元素

        Args:
            xpath: 元素的 XPath 路径

        Returns:
            是否成功点击
        """
        logger.debug(f"Clicking element by XPath: {xpath}")

        try:
            locator = self.page.locator(f"xpath={xpath}")
            await locator.first.click()
            await self.wait_for_navigation()
            return True
        except Exception as e:
            logger.warning(f"Failed to click by XPath: {xpath}, error: {e}")
            return False

    async def input_by_xpath(self, xpath: str, text: str, clear_first: bool = True) -> bool:
        """
        通过 XPath 在元素中输入文本

        Args:
            xpath: 元素的 XPath 路径
            text: 要输入的文本
            clear_first: 是否先清空

        Returns:
            是否成功输入
        """
        logger.debug(f"Inputting text by XPath: {xpath}, text: '{text}'")

        try:
            locator = self.page.locator(f"xpath={xpath}")
            
            if clear_first:
                await locator.first.fill(text)
            else:
                await locator.first.type(text)
            
            return True
        except Exception as e:
            logger.warning(f"Failed to input by XPath: {xpath}, error: {e}")
            return False

    async def scroll_element_into_view(
        self,
        x: float,
        y: float,
        block: str = 'center',
        behavior: str = 'instant'
    ) -> bool:
        """
        将指定坐标的元素滚动到视口中
        
        对应 JS 版本: node.scrollIntoView({ behavior: 'instant', block: 'center' })
        
        Args:
            x: 元素中心 X 坐标
            y: 元素中心 Y 坐标
            block: 垂直对齐方式 (start/center/end/nearest)
            behavior: 滚动行为 (instant/smooth/auto)
        
        Returns:
            是否成功滚动
        """
        logger.debug(f"Scrolling element at ({x}, {y}) into view (block={block}, behavior={behavior})")

        self._raise_if_popup_redirect_failed()
        try:
            # 执行 JS 代码：获取元素并滚动到视口中心
            result = await self.page.evaluate("""
                (coords) => {
                    const { x, y, block, behavior } = coords;
                    const element = document.elementFromPoint(x, y);
                    
                    if (!element) {
                        return { success: false, reason: 'Element not found at coordinates' };
                    }
                    
                    // 检查元素是否在视口中
                    const rect = element.getBoundingClientRect();
                    const isInViewport = (
                        rect.top >= 0 &&
                        rect.left >= 0 &&
                        rect.bottom <= window.innerHeight &&
                        rect.right <= window.innerWidth
                    );
                    
                    if (isInViewport) {
                        return { success: true, reason: 'Element already in viewport', scrolled: false };
                    }
                    
                    // 滚动元素到视口中心（与 JS 版本完全一致）
                    element.scrollIntoView({ 
                        behavior: behavior,  // 'instant' - 立即滚动，无动画
                        block: block         // 'center' - 垂直居中
                    });
                    
                    return { success: true, reason: 'Element scrolled into view', scrolled: true };
                }
            """, {"x": x, "y": y, "block": block, "behavior": behavior})
            
            if result.get('scrolled'):
                logger.info(f"Element scrolled into view: {result.get('reason')}")
                # 等待滚动完成
                await asyncio.sleep(0.3)
            else:
                logger.debug(f"No scroll needed: {result.get('reason')}")
            
            return result.get('success', False)
            
        except Exception as e:
            logger.warning(f"Failed to scroll element into view: {e}")
            return False

    async def scroll_element_by_xpath_into_view(
        self,
        xpath: str,
        block: str = 'center',
        behavior: str = 'instant'
    ) -> bool:
        """
        通过 XPath 将元素滚动到视口中（与 JS 版本完全对齐）
        
        对应 JS 版本: getElementInfoByXpath 中的自动滚动逻辑
        
        Args:
            xpath: 元素的 XPath 路径
            block: 垂直对齐方式 (start/center/end/nearest)
            behavior: 滚动行为 (instant/smooth/auto)
        
        Returns:
            是否成功滚动
        """
        logger.debug(f"Scrolling element by XPath into view: {xpath}")

        self._raise_if_popup_redirect_failed()
        try:
            # 执行 JS 代码：通过 XPath 获取元素并滚动
            result = await self.page.evaluate("""
                (params) => {
                    const { xpath, block, behavior } = params;
                    
                    // 通过 XPath 查找元素
                    const xpathResult = document.evaluate(
                        xpath,
                        document,
                        null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE,
                        null
                    );
                    
                    const element = xpathResult.singleNodeValue;
                    
                    if (!element || !(element instanceof Element)) {
                        return { success: false, reason: 'Element not found or not an Element type' };
                    }
                    
                    // 检查元素是否部分在视口中（与 JS 版本逻辑一致）
                    const rect = element.getBoundingClientRect();
                    const isPartiallyInViewport = (
                        rect.bottom > 0 &&
                        rect.right > 0 &&
                        rect.top < window.innerHeight &&
                        rect.left < window.innerWidth
                    );
                    
                    if (isPartiallyInViewport) {
                        return { success: true, reason: 'Element already partially in viewport', scrolled: false };
                    }
                    
                    // 🔑 关键：滚动到视口中心（与 JS 版本完全一致）
                    element.scrollIntoView({ 
                        behavior: behavior,  // 'instant' - 立即滚动
                        block: block         // 'center' - 垂直居中
                    });
                    
                    return { success: true, reason: 'Element scrolled into view', scrolled: true };
                }
            """, {"xpath": xpath, "block": block, "behavior": behavior})
            
            if result.get('scrolled'):
                logger.info(f"Element scrolled into view by XPath: {result.get('reason')}")
                # 等待滚动完成
                await asyncio.sleep(0.3)
            else:
                logger.debug(f"No scroll needed: {result.get('reason')}")
            
            return result.get('success', False)
            
        except Exception as e:
            logger.warning(f"Failed to scroll element by XPath into view: {e}")
            return False

    async def execute_script(self, script: str) -> Any:
        """
        执行 JavaScript 脚本（别名方法，与 execute_javascript 一致）
        
        Args:
            script: JavaScript 代码
        
        Returns:
            执行结果
        """
        return await self.evaluate_javascript(script)


__all__ = ["WebPage"]
