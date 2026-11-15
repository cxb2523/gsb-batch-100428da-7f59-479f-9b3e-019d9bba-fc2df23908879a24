"""
抖音直播数据采集器 - 轻量版

支持两种模式:
1. CDP 模式: 连接已有 Chrome 浏览器 (推荐, 复用登录态)
2. HTTP 模式: 直接请求 (需要有效 Cookie)
"""
import asyncio
import json
import re
import random
import time
from datetime import datetime
from html import unescape
from typing import Optional, Dict, Any, Tuple
from urllib.parse import quote, urlparse

import httpx
from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)


class DouyinLiveCollector:
    """抖音直播数据采集器"""

    def __init__(
        self,
        browser_mode: str = "cdp",
        cdp_port: int = 9222,
        cookie_str: str = "",
        proxy_url: str = "",
    ):
        self.browser_mode = browser_mode
        self.cdp_port = cdp_port
        self.cookie_str = cookie_str
        self.proxy_url = proxy_url

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._is_ready = False
        # 启动阶段浏览器不可用时的降级原因; None 表示未发生启动降级
        self._startup_degrade_reason: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    async def start(self):
        """启动采集器"""
        # 初始化 HTTP 客户端
        client_kw = {"timeout": httpx.Timeout(30.0), "follow_redirects": True}
        if self.proxy_url:
            client_kw["proxies"] = self.proxy_url
        self._http_client = httpx.AsyncClient(**client_kw)

        # 启动 Playwright, 失败则降级为 HTTP-only
        if self.browser_mode == "cdp":
            try:
                await self._start_cdp()
            except Exception as e:
                logger.warning(f"[LiveCollector] CDP 启动失败: {e}, 降级为 HTTP 模式")
                self._startup_degrade_reason = (
                    f"startup_browser_unavailable: CDP 启动失败 ({type(e).__name__}: {e})"
                )
                self.browser_mode = "http"
        elif self.browser_mode == "headless":
            try:
                await self._start_headless()
            except Exception as e:
                logger.warning(f"[LiveCollector] 无头浏览器启动失败: {e}, 降级为 HTTP 模式")
                self._startup_degrade_reason = (
                    f"startup_browser_unavailable: 无头浏览器启动失败 ({type(e).__name__}: {e})"
                )
                self.browser_mode = "http"

        self._is_ready = True
        logger.info(f"[LiveCollector] 启动完成, mode={self.browser_mode}")
        # 文件日志
        try:
            self._log_file = open("/tmp/douyin-live-monitor/data/collector.log", "a")
            self._log_file.write(f"[{datetime.now()}] 启动完成 mode={self.browser_mode}\n")
            self._log_file.flush()
        except Exception:
            self._log_file = None

    def _log(self, msg: str):
        """写文件日志"""
        try:
            if hasattr(self, '_log_file') and self._log_file:
                self._log_file.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
                self._log_file.flush()
        except Exception:
            pass

    async def _start_cdp(self):
        """CDP 模式连接已有 Chrome"""
        cdp_url = f"http://127.0.0.1:{self.cdp_port}"
        logger.info(f"[LiveCollector] 连接 CDP: {cdp_url}")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)

        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else await self._browser.new_context()

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()

        # 检查登录状态
        try:
            await self._page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            logged_in = await self._check_login_state()
            logger.info(f"[LiveCollector] CDP 登录状态: {'已登录' if logged_in else '未登录'}")
        except Exception as e:
            logger.warning(f"[LiveCollector] 导航到抖音失败: {e}")

    async def _start_headless(self):
        """无头模式"""
        self._playwright = await async_playwright().start()
        launch_kw = {"headless": True}
        if self.proxy_url:
            launch_kw["proxy"] = {"server": self.proxy_url}

        self._browser = await self._playwright.chromium.launch(**launch_kw)
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        # 注入 Cookie（在导航之前！这样页面请求自带登录态）
        if self.cookie_str:
            logger.info(f"[LiveCollector] 尝试注入 Cookie ({len(self.cookie_str)} 字符)")
            await self._inject_cookies()
        else:
            logger.info("[LiveCollector] 无 Cookie，使用未登录模式")

        self._page = await self._context.new_page()

    async def _inject_cookies(self):
        """注入 Cookie 到浏览器（在导航之前调用）"""
        try:
            cookies_to_add = []
            for part in self.cookie_str.split(";"):
                if "=" not in part:
                    continue
                name, value = part.strip().split("=", 1)
                # 只注入 douyin 相关域名
                for domain in [".douyin.com", "douyin.com", "www.douyin.com", "live.douyin.com"]:
                    cookies_to_add.append({
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": domain,
                        "path": "/",
                    })
            await self._context.add_cookies(cookies_to_add)
            logger.info(f"[LiveCollector] 已注入 {len(cookies_to_add)} 个 Cookie（覆盖{len(cookies_to_add)//4}个域名）")
        except Exception as e:
            logger.warning(f"[LiveCollector] Cookie 注入失败: {e}")

    async def _check_login_state(self) -> bool:
        """检查抖音登录状态"""
        if not self._page:
            return False
        try:
            ls = await self._page.evaluate("() => window.localStorage")
            if isinstance(ls, dict) and ls.get("HasUserLogin") == "1":
                return True
        except Exception:
            pass
        try:
            cookies = await self._context.cookies()
            for c in cookies:
                if c["name"] == "LOGIN_STATUS" and c["value"] == "1":
                    return True
        except Exception:
            pass
        return False

    async def fetch_live_data(self, url: str) -> Dict[str, Any]:
        """获取直播间数据

        Args:
            url: 抖音直播间 URL, 例如 https://live.douyin.com/123456789

        Returns:
            {
                "is_live": bool,
                "title": str,
                "viewers": int or None,
                "likes": int or None,
                "comments": int or None,
                "shares": int or None,
                "products": [...],
                "error": str or None,
                "source": "browser" | "http",
                "degraded": bool,
                "degrade_reason": str or None,
                "fallback_reason": str or None  # degrade_reason 的别名
            }

        来源标记说明:
            source 仍然只表示本次实际使用的采集通道 (browser/http), 保持原有语义。
            degraded 为 True 表示浏览器通道已尝试但失败, 本次数据来自 HTTP 兜底;
            degrade_reason 记录失败类别与异常信息, 便于事后核对。
        """
        # 优先用浏览器采集
        if self.browser_mode == "cdp" and self._page:
            return await self._fetch_with_browser(url)
        elif self.browser_mode == "headless" and self._page:
            return await self._fetch_with_browser(url)
        else:
            result = await self._fetch_with_http(url)
            if self._startup_degrade_reason:
                # 浏览器在启动阶段就不可用, 本次数据的来源需要标注为降级
                return self._mark_degraded(result, self._startup_degrade_reason)
            return result

    async def _fetch_with_browser(self, url: str) -> Dict[str, Any]:
        """用浏览器采集; 失败时回落 HTTP 并记录降级原因"""
        try:
            start = time.time()
            await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(3, 5))

            # 滑到底部触发数据加载
            for _ in range(3):
                await self._page.evaluate("window.scrollBy(0, 500)")
                await asyncio.sleep(0.5)

            # 获取页面文本
            html = await self._page.content()
            title = await self._page.title()
            body_text = await self._page.evaluate("() => document.body.innerText")
            elapsed = time.time() - start
            self._log(f"[Browser] {url[:50]}.. title={title} body={len(body_text)}c耗时{elapsed:.1f}s")
            return await self._parse_live_data(html, url, source="browser", body_text=body_text, page_title=title)

        except Exception as e:
            reason = self._describe_browser_failure(e)
            self._log(f"[Browser] 失败({reason}): {url[:60]}")
            logger.warning(f"[Browser] 采集失败: {e}, 回落 HTTP 模式")
            result = await self._fetch_with_http(url)
            return self._mark_degraded(result, reason)

    @staticmethod
    def _describe_browser_failure(exc: BaseException) -> str:
        """把浏览器异常归纳成可核对的降级原因

        返回值同时包含机器可读的代码 (timeout / navigation_error / ...)
        和原始异常类型与信息, 便于日志与数据库排查。
        """
        exc_name = type(exc).__name__
        exc_name_lower = exc_name.lower()
        message = str(exc) or ""
        message_lower = message.lower()

        if (
            isinstance(exc, asyncio.TimeoutError)
            or "timeout" in exc_name_lower
            or "timeout" in message_lower
            or "timed out" in message_lower
        ):
            kind, label = "timeout", "浏览器采集超时"
        elif (
            "net::" in message_lower
            or "err_" in message_lower
            or "navigation" in message_lower
            or "navigat" in exc_name_lower
        ):
            kind, label = "navigation_error", "浏览器导航异常"
        elif (
            "target closed" in message_lower
            or "disconnected" in message_lower
            or "has been closed" in message_lower
        ):
            kind, label = "browser_unavailable", "浏览器连接不可用"
        else:
            kind, label = "browser_error", "浏览器采集异常"

        detail = f"{exc_name}: {message}".strip().rstrip(":")
        if len(detail) > 300:
            detail = detail[:300] + "..."
        return f"{kind}: {label} ({detail})"

    @staticmethod
    def _mark_degraded(result: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """给 HTTP 兜底结果打上降级标记

        source 保持原有取值 (本次数据来自 http), degraded/degrade_reason
        作为附加信息, 不覆盖已有 error 等字段。
        """
        result.setdefault("source", "http")
        result["degraded"] = True
        result["degrade_reason"] = reason
        # 别名: 便于外部消费者按 fallback 语义读取, 值保持一致
        result["fallback_reason"] = reason
        return result

    async def _fetch_with_http(self, url: str) -> Dict[str, Any]:
        """用 HTTP 采集

        返回值始终包含来源标记字段:
            source: "http"
            degraded: 默认 False, 由浏览器失败回落时由 _mark_degraded 置为 True
            degrade_reason: 默认 None
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
            "Accept": "text/html,application/json,text/plain,*/*",
        }
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str

        result: Dict[str, Any] = {
            "is_live": False,
            "source": "http",
            "url": url,
            "degraded": False,
            "degrade_reason": None,
            "fallback_reason": None,
        }

        try:
            resp = await self._http_client.get(url, headers=headers)
            text = resp.text

            # 检测风控: 命中后直接返回, 不让后续解析覆盖风控错误
            if resp.status_code in (403, 429):
                result["error"] = f"HTTP {resp.status_code} 被风控"
                return result
            if any(kw in text for kw in ("验证码", "安全验证", "滑块")):
                result["error"] = "触发了验证码"
                return result

            parsed = await self._parse_live_data(
                text, url, source="http", page_title=self._extract_page_title(text)
            )
            # 解析结果缺失部分指标时保留已解析到的字段, 只补齐来源/降级标记
            parsed.setdefault("source", "http")
            parsed.setdefault("url", url)
            parsed.setdefault("degraded", False)
            parsed.setdefault("degrade_reason", None)
            parsed.setdefault("fallback_reason", parsed["degrade_reason"])
            return parsed
        except Exception as e:
            result["error"] = str(e)
            return result

    @staticmethod
    def _extract_page_title(html: str) -> str:
        """从 HTML 中提取可用于判断直播状态的 <title>

        只在标题明确是直播间页面 (含 "直播间") 时返回, 避免未开播页的
        通用站点标题 "抖音直播" 被误判为正在直播。
        """
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if not m:
            return ""
        title = unescape(m.group(1)).strip()
        return title if "直播间" in title else ""

    async def _parse_live_data(self, html: str, url: str, source: str = "http",
                         body_text: str = "", page_title: str = "") -> Dict[str, Any]:
        """从页面 HTML + 渲染文本解析直播数据

        解析失败或指标缺失时保留已解析到的字段 (缺的为 None), 不追加降级信息；
        降级标记由调用方 (浏览器回落路径) 负责补写。
        """
        text = unescape(html)
        result = {
            "is_live": False,
            "source": source,
            "url": url,
            "degraded": False,
            "degrade_reason": None,
            "fallback_reason": None,
        }
        # 初始化字段
        for k in ("title", "room_id", "web_rid", "viewers", "likes", "comments", "shares", "error"):
            result[k] = None
        result["products"] = []

        # ---- 判断是否在直播 ----
        # 方式1: 标题含 "抖音直播" -> 肯定在播
        is_live = "抖音直播" in page_title or "直播间" in page_title

        # 方式2: 页面文本包含直播特征
        if not is_live and body_text:
            is_live = any(t in body_text for t in (
                "在线观众", "本场点赞", "本场直播", "直播中",
                "LIVE", "live_status",
            ))

        # 方式3: 从 JSON 块里找
        if not is_live:
            json_blocks = self._extract_json_blocks(text)
            merged = "\n".join(json.dumps(b, ensure_ascii=False) for b in json_blocks[:6]) if json_blocks else ""
            is_live = any(t in merged for t in (
                '"status":"2"', '"live_status":"2"', '"live_status":"LIVE"'
            ))

        result["is_live"] = is_live
        if not is_live:
            result["error"] = "未在直播"
            return result

        # ---- 从页面文本提取数据（优先，因为 JSON 块可能不存在） ----
        source_text = body_text or text[:80000]

        # 标题
        if not result.get("title"):
            result["title"] = page_title.replace(" - 抖音直播", "").replace("的抖音直播间", "") if page_title else ""

        # 房间 ID 从 URL 提取
        m = re.search(r"live\.douyin\.com/(\d+)", url)
        result["room_id"] = m.group(1) if m else None

        # 在线观众: "在线观众 · 数字" 或 "在线观众\n数字"
        if result["viewers"] is None:
            m = re.search(r"在线观众\s*[·:：]?\s*(\d[\d,.]*)", source_text)
            if m:
                result["viewers"] = self._parse_number(m.group(1))

        # 点赞: "N万本场点赞" 或 "数字本场点赞"
        if result["likes"] is None:
            m = re.search(r"([\d.]+)万本场点赞", source_text)
            if m:
                result["likes"] = int(float(m.group(1)) * 10000)
            else:
                m = re.search(r"(\d+)本场点赞", source_text)
                if m:
                    result["likes"] = int(m.group(1))

        # 尝试调抖音内部 Room API 获取更多数据
        if result["room_id"]:
            await self._try_room_api(result)

        # 降级到 JSON 块提取
        json_blocks = self._extract_json_blocks(text)
        merged = "\n".join(json.dumps(b, ensure_ascii=False) for b in json_blocks[:6]) if json_blocks else ""

        if merged:
            if not result.get("title"):
                result["title"] = self._extract_field(merged, [
                    r'"title"\s*:\s*"([^"]{1,120})"',
                    r'"room_title"\s*:\s*"([^"]{1,120})"',
                ]) or ""
            if not result.get("room_id"):
                result["room_id"] = self._extract_field(merged, [
                    r'"room_id"\s*:\s*"?(\d+)"?',
                    r'"roomId"\s*:\s*"?(\d+)"?',
                ])
            if result["viewers"] is None:
                result["viewers"] = self._extract_int(merged, ["user_count", "viewer_count", "room_user_count", "online_count"])
            if result["likes"] is None:
                result["likes"] = self._extract_int(merged, ["like_count", "digg_count", "total_like_count"])
            if result["comments"] is None:
                result["comments"] = self._extract_int(merged, ["comment_count", "comments_count"])
            if result["shares"] is None:
                result["shares"] = self._extract_int(merged, ["share_count", "share_total"])
            result["products"] = self._extract_products(merged)

        return result

    def _extract_json_blocks(self, html: str):
        blocks = []
        patterns = [
            r'<script id="RENDER_DATA" type="application/json">(.*?)</script>',
            r'<script id="SIGI_STATE" type="application/json">(.*?)</script>',
            r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.S):
                raw = unescape(match.group(1))
                try:
                    if "%" in raw[:200]:
                        from urllib.parse import unquote
                        raw = unquote(raw)
                    blocks.append(json.loads(raw))
                except Exception:
                    continue
        return blocks

    def _extract_field(self, text: str, patterns) -> Optional[str]:
        for p in patterns:
            m = re.search(p, text, re.S)
            if m:
                return m.group(1)
        return None

    def _extract_int(self, text: str, keys) -> Optional[int]:
        for key in keys:
            v = self._extract_field(text, [rf'"{key}"\s*:\s*"?([0-9.]+万?)"?'])
            if v is not None:
                return self._parse_number(v)
        return None

    def _parse_number(self, v) -> Optional[int]:
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v).replace(",", "").strip()
        if not s:
            return None
        mul = 10000 if s.endswith("万") else 1
        s = s.rstrip("万")
        try:
            return int(float(s) * mul)
        except ValueError:
            return None

    def _extract_products(self, text: str):
        products = []
        for m in re.finditer(r'"title"\s*:\s*"([^"]{2,80})"[^{}]{0,300}"price"\s*:\s*"?([0-9.]+)"?', text):
            products.append({"name": m.group(1), "price": float(m.group(2))})
            if len(products) >= 20:
                break
        return products

    async def _try_room_api(self, result: Dict[str, Any]):
        """调用抖音内部 Room API 补充数据（评论数、分享数等）"""
        room_id = result.get("room_id")
        if not room_id:
            return

        api_url = f"https://www.douyin.com/aweme/v1/web/live/room/info/?aid=6383&room_id={room_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.douyin.com/",
        }
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str

        try:
            resp = await self._http_client.get(api_url, headers=headers)
            if resp.status_code != 200:
                return
            data = resp.json()
            room_info = data.get("data", {}) or data.get("room", {})

            if result.get("title") is None:
                result["title"] = room_info.get("title", "")
            if result.get("viewers") is None:
                result["viewers"] = self._parse_number(
                    room_info.get("user_count") or room_info.get("viewer_count")
                )
            if result.get("likes") is None:
                result["likes"] = self._parse_number(room_info.get("like_count"))
            if result.get("comments") is None:
                result["comments"] = self._parse_number(room_info.get("comment_count"))
            if result.get("shares") is None:
                result["shares"] = self._parse_number(room_info.get("share_count"))

            # 商品列表
            products = room_info.get("products") or room_info.get("room_products") or []
            if products:
                result["products"] = [
                    {"name": p.get("title", ""), "price": p.get("price", 0)}
                    for p in products[:20]
                ]

            self._log(f"[API] room_id={room_id} -> 评论={result['comments']} 分享={result['shares']} 商品={len(result['products'])}")
        except Exception as e:
            self._log(f"[API] room_id={room_id} 失败: {e}")

    async def close(self):
        """关闭采集器"""
        self._is_ready = False
        if self._http_client:
            await self._http_client.aclose()
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        logger.info("[LiveCollector] 已关闭")
