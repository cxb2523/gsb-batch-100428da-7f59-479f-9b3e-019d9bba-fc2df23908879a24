# SPDX-License-Identifier: MIT

from typing import Optional, Union, Dict, Any
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None
from url_to_llm_text.config import ScraperConfig, load_config, parse_proxy

TRACKER_DOMAINS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net",
    "connect.facebook.net",
    "hotjar.com",
    "clarity.ms",
    "criteo.net",
    "adroll.com",
    "scorecardresearch.com",
    "analytics.twitter.com"
)

async def get_page_source(
    url: str,
    wait: Optional[float] = None,
    user_agent: Optional[str] = None,
    proxy: Optional[Union[str, Dict[str, Any]]] = None,
    config: Optional[Union[ScraperConfig, Dict[str, Any], str]] = None
) -> str:
    """
    Get html text using Playwright with optional proxy support, bandwidth optimization,
    anti-bot stealth parameters, and configurable waiting/scrolling.

    Args:
        url (str): The url from which html content is to be extracted.
        wait (float, optional): Extra seconds to wait after load. Defaults to config.post_load_wait.
        user_agent (str, optional): Custom user agent. Defaults to modern Chrome in config.
        proxy (str or dict, optional): Proxy URL string ("http://user:pass@host:port") or Playwright proxy dict.
        config (ScraperConfig, dict, or str path, optional): ScraperConfig instance, dictionary, or YAML/JSON path.

    Returns:
        str: Raw HTML page source.
    """
    try:
        cfg = load_config(config)

        # Allow explicit function arguments to override config
        if proxy:
            proxy_config = parse_proxy(proxy)
        else:
            proxy_config = parse_proxy(cfg.proxy)

        post_load_wait = wait if wait is not None else cfg.post_load_wait

        # Use modern UA by default, unless user explicitly passed a custom UA
        ua = user_agent if user_agent else cfg.user_agent

        if async_playwright is None:
            raise ImportError(
                "Playwright is not installed. Please install it using:\n"
                "pip install pytest-playwright\n"
                "playwright install"
            )

        playwright = await async_playwright().start()
        chromium = playwright.chromium

        launch_kwargs = {
            "headless": cfg.headless,
            "args": [
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ]
        }
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        browser = await chromium.launch(**launch_kwargs)

        context_kwargs = {
            "user_agent": ua,
            "device_scale_factor": 1,
        }
        if cfg.viewport:
            context_kwargs["viewport"] = cfg.viewport
        if cfg.locale:
            context_kwargs["locale"] = cfg.locale
        if cfg.timezone_id:
            context_kwargs["timezone_id"] = cfg.timezone_id

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        # Intercept network requests to block bandwidth-heavy assets and trackers
        async def handle_route(route):
            req = route.request
            resource_type = req.resource_type
            req_url = req.url.lower()

            if cfg.block_trackers and any(t in req_url for t in TRACKER_DOMAINS):
                await route.abort()
                return

            if cfg.block_media and resource_type in ("media",):
                await route.abort()
                return

            if cfg.block_fonts and resource_type in ("font",):
                await route.abort()
                return

            if cfg.block_images and resource_type in ("image",):
                await route.abort()
                return

            await route.continue_()

        if cfg.block_trackers or cfg.block_media or cfg.block_fonts or cfg.block_images:
            await page.route("**/*", handle_route)

        # Navigate
        await page.goto(url, wait_until=cfg.wait_until, timeout=cfg.timeout)
        await page.wait_for_timeout(post_load_wait * 1000)

        # Scroll to trigger lazy-loaded images / infinite lists
        if cfg.scroll_down:
            for _ in range(max(1, cfg.scroll_times)):
                await page.keyboard.press("PageDown")
                await page.wait_for_timeout(post_load_wait * 1000)

        page_source = await page.content()
        await browser.close()
        await playwright.stop()

        return page_source

    except Exception as e:
        print("Error while getting page source: ", str(e))
        return ""
