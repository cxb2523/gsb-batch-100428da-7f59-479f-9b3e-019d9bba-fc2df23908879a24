#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RSC (React Server Components) adapter for intercepting Next.js RSC requests to get Markdown content directly.

The adapter is the only place that talks to Playwright.  All payload parsing
lives in :mod:`src.infrastructure.gateway.rsc_parser` so it can be exercised
without a browser or network access.
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlparse

from src.domain.models.wiki_page import NavigationPage, WikiContent
from src.domain.services.navigation_builder import navigation_from_content
from src.infrastructure.gateway.rsc_parser import parse_rsc_response

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page, Response


def _load_playwright():
    """Import Playwright lazily.

    Keeping the browser dependency out of module import time means the CLI and
    the pure parsing logic stay importable even where Playwright is unavailable
    (e.g. browser binaries not installed).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Playwright is required for live fetching. "
            "Install it with 'uv sync' and 'python -m playwright install chromium'."
        ) from exc
    return async_playwright


class RSCAdapter:
    """
    适配器，用于拦截Next.js的RSC请求以直接获取Markdown内容。
    """

    def __init__(self):
        # RSC请求URL -> RSC文本内容
        self.intercepted_data: Dict[str, str] = {}
        self.rsc_param: Optional[str] = None

    async def fetch_wiki_content_via_rsc(self, base_url: str) -> WikiContent:
        """
        通过拦截RSC请求获取Wiki内容。

        Args:
            base_url: Wiki的基础URL (例: https://deepwiki.com/facebook/react)

        Returns:
            WikiContent: 导航页面列表与RSC中提取的页面内容。
        """
        wiki_content = WikiContent()
        async_playwright = _load_playwright()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                await self._setup_request_interceptor(page)

                print(f"open url {base_url}...")
                await page.goto(base_url, wait_until="networkidle", timeout=60000)

                print(f"Intercepted {len(self.intercepted_data)} RSC requests so far")
                for url in self.intercepted_data.keys():
                    print(f"  - Intercepted: {url}")

                print("Extracting navigation info...")
                navigation_pages = await self._extract_navigation_info(page, base_url)
                print(f"Found {len(navigation_pages)} navigation links")

                content_pages = ()
                if navigation_pages:
                    first_page_url = navigation_pages[0].url
                    rsc_url = self._construct_rsc_url(first_page_url)
                    if rsc_url:
                        parsed = await self._fetch_rsc_content(page, rsc_url)
                        if parsed is not None:
                            content_pages = parsed.content_pages

                # 导航链接缺失时，退回到 RSC 元数据中的页面列表，确保每个页面
                # 仍有明确结局而不是静默地什么都不写。
                if not navigation_pages and content_pages:
                    navigation_pages = navigation_from_content(
                        content_pages, base_url
                    )
                    print(
                        f"Derived {len(navigation_pages)} navigation pages from "
                        "RSC page metadata"
                    )

                wiki_content = WikiContent(
                    navigation_pages=navigation_pages,
                    content_pages=content_pages,
                )

            except Exception as e:
                print(f"Error during RSC interception: {e}")
                raise
            finally:
                await browser.close()

        return wiki_content

    async def _setup_request_interceptor(self, page: Page):
        """设置请求拦截器来捕获RSC请求"""
        print("Setting up request interceptor...")

        async def handle_response(response: Response):
            """处理响应，捕获RSC数据"""
            url = response.url

            if "_rsc=" in url:
                print(f"RSC Response: {response.status} {url[:100]}...")
                rsc_param = url.split("_rsc=")[1]
                print(f"获取到rsc_param: {rsc_param}")
                self.rsc_param = rsc_param
                if response.status == 200:
                    try:
                        content_type = response.headers.get("content-type", "")
                        print(f"content-type: {content_type}")

                        content = await response.text()
                        self.intercepted_data[url] = content
                        print(
                            "Successfully intercepted RSC response: "
                            f"{len(content)} chars"
                        )
                    except Exception as e:
                        print(f"Error processing RSC response: {e}")
                else:
                    print(f"RSC request failed with status: {response.status}")

        async def handle_request(request):
            """处理请求，记录所有请求"""
            url = request.url
            if "_rsc=" in url:
                print(f"RSC Request: {request.method} {url}")

        page.on("response", handle_response)
        page.on("request", handle_request)
        print("Request interceptor setup complete")

    async def _extract_navigation_info(
        self, page: Page, base_url: str
    ) -> List[NavigationPage]:
        """从页面中提取导航信息"""
        navigation_info: List[NavigationPage] = []

        try:
            print("Looking for navigation links...")
            selectors = [
                'nav a[href*="/"]',
                '.sidebar a[href*="/"]',
                '.navigation a[href*="/"]',
                'a[href*="/"]',
            ]

            all_links = []
            for selector in selectors:
                links = await page.query_selector_all(selector)
                all_links.extend(links)

            seen_hrefs = set()
            nav_links = []
            for link in all_links:
                href = await link.get_attribute("href")
                if href and href not in seen_hrefs:
                    seen_hrefs.add(href)
                    nav_links.append(link)

            print(f"Total unique links found: {len(nav_links)}")

            for link in nav_links:
                href = await link.get_attribute("href")
                text = await link.text_content()

                if href and text and href.strip() and text.strip():
                    if href.startswith("/"):
                        full_url = urljoin(base_url, href)
                    else:
                        full_url = href

                    if self._is_same_wiki_page(base_url, full_url):
                        navigation_info.append(
                            NavigationPage(
                                title=text.strip(),
                                url=full_url,
                                path=href,
                            )
                        )

            seen_urls = set()
            unique_navigation = []
            for item in navigation_info:
                key = unquote(item.url).rstrip("/")
                if key not in seen_urls:
                    seen_urls.add(key)
                    unique_navigation.append(item)

            print("--------------找到的所有页面链接------------------")
            for link in unique_navigation:
                print(f"Navigation link: {link.title} -> {link.url}")
            print("--------------------------------")
            return unique_navigation

        except Exception as e:
            print(f"Error extracting navigation: {e}")
            return []

    def _is_same_wiki_page(self, base_url: str, target_url: str) -> bool:
        """检查目标URL是否属于同一个wiki"""
        base_parsed = urlparse(base_url)
        target_parsed = urlparse(target_url)

        if base_parsed.netloc != target_parsed.netloc:
            return False

        base_path_parts = base_parsed.path.strip("/").split("/")
        target_path_parts = target_parsed.path.strip("/").split("/")

        if len(base_path_parts) >= 2 and len(target_path_parts) >= 2:
            return (
                base_path_parts[0] == target_path_parts[0]
                and base_path_parts[1] == target_path_parts[1]
            )

        return False

    def _construct_rsc_url(self, page_url: str) -> Optional[str]:
        """构造RSC请求URL"""
        try:
            parsed = urlparse(page_url)
            if parsed.query:
                return f"{page_url}&_rsc={self.rsc_param}"
            return f"{page_url}?_rsc={self.rsc_param}"
        except Exception as e:
            print(f"Error constructing RSC URL: {e}")
            return None

    async def _fetch_rsc_content(
        self, page: Page, rsc_url: str
    ) -> Optional[WikiContent]:
        """获取RSC内容并解析所有页面的Markdown"""
        try:
            # The interceptor already saw this request while the first page
            # loaded; prefer the raw payload over re-navigating the browser.
            if rsc_url in self.intercepted_data:
                parsed = parse_rsc_response(self.intercepted_data[rsc_url])
                if parsed.content_pages:
                    return parsed

            response = await page.goto(rsc_url, wait_until="networkidle", timeout=30000)
            if response and response.status == 200:
                # A direct RSC request returns the raw flight payload as text;
                # ``page.content()`` would wrap it in an HTML document.
                try:
                    raw = await response.text()
                except Exception:
                    raw = ""
                parsed = parse_rsc_response(raw)
                if not parsed.content_pages:
                    parsed = parse_rsc_response(await page.content())
                return parsed

            print(f"直接请求rsc {rsc_url} 失败，尝试从拦截的数据中获取")
            if rsc_url in self.intercepted_data:
                return parse_rsc_response(self.intercepted_data[rsc_url])

            print(f"从拦截的数据中获取 {rsc_url} 失败")
            return None

        except Exception as e:
            print(f"Error fetching RSC content: {e}")
            return None
