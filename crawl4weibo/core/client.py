#!/usr/bin/env python

"""
Weibo Crawler Client - Based on successfully tested code
"""

import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from ..exceptions.base import CrawlError, NetworkError, ParseError, UserNotFoundError
from ..models.batch import BatchResult, PageFailure, PageFailureReason
from ..models.comment import Comment
from ..models.post import Post
from ..models.user import User
from ..utils.cookie_fetcher import LOGIN_COOKIE_NAMES, CookieFetcher
from ..utils.downloader import ImageDownloader, VideoDownloader
from ..utils.logger import setup_logger
from ..utils.parser import WeiboParser
from ..utils.proxy import ProxyPool, ProxyPoolConfig
from ..utils.rate_limit import RateLimitConfig, rate_limit
from ..utils.user_filters import filter_users


class WeiboClient:
    """Weibo Crawler Client"""

    def __init__(
        self,
        cookies: str | dict[str, str] | None = None,
        log_level: str = "INFO",
        log_file: str | None = None,
        user_agent: str | None = None,
        proxy_config: ProxyPoolConfig | None = None,
        rate_limit_config: RateLimitConfig | None = None,
        use_browser_cookies: bool = True,
        auto_fetch_cookies: bool = True,
        login_cookies: bool = False,
        cookie_storage_path: str | Path | None = None,
        browser_headless: bool = True,
        login_timeout: int = 120,
    ):
        """
        Initialize Weibo client

        Args:
            cookies: Optional cookie string or dictionary. If provided,
                auto_fetch_cookies will be ignored.
            log_level: Logging level
            log_file: Log file path
            user_agent: Optional User-Agent string
            proxy_config: Proxy pool configuration object. If not provided,
                proxy will be disabled. Use ProxyPoolConfig to configure
                proxy settings like API URL, TTL, pool size, etc.
            rate_limit_config: Rate limiting configuration. If not provided,
                uses default configuration that automatically adjusts delays
                based on proxy pool size. Larger pools = shorter delays.
            use_browser_cookies: If True, uses Playwright to fetch cookies
                from a real browser. Requires playwright installation.
                If False, uses simple requests method (may not work if
                Weibo has strengthened anti-scraping). Default: True
            auto_fetch_cookies: If True and cookies parameter is not provided,
                automatically fetches cookies during initialization.
                Default: True
            login_cookies: If True, waits for logged-in cookies via Playwright
                browser automation (headful when needed). Default: False
            cookie_storage_path: Optional path to persist Playwright storage state
                for reusing logged-in cookies across runs.
            browser_headless: Whether to run Playwright in headless mode.
            login_timeout: Timeout for manual login in seconds.
        """
        self.logger = setup_logger(
            level=getattr(__import__("logging"), log_level.upper()), log_file=log_file
        )

        self.session = requests.Session()

        default_user_agent = (
            "Mozilla/5.0 (Linux; Android 13; SM-G9980) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/112.0.5615.135 Mobile Safari/537.36"
        )
        self.user_agent = user_agent or default_user_agent
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Referer": "https://m.weibo.cn/",
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            }
        )

        if login_cookies and not use_browser_cookies:
            self.logger.info(
                "login_cookies requires browser cookies. "
                "Enabling browser-based cookie fetching."
            )
            use_browser_cookies = True

        if login_cookies and cookie_storage_path is None:
            cookie_storage_path = "~/.crawl4weibo/weibo_storage_state.json"

        self.cookie_storage_path = (
            Path(cookie_storage_path).expanduser()
            if cookie_storage_path is not None
            else None
        )
        self.browser_headless = browser_headless
        self.login_timeout = login_timeout
        self.login_cookies = login_cookies
        self.use_browser_cookies = use_browser_cookies

        if (
            self.login_cookies
            and self.browser_headless
            and not (self.cookie_storage_path and self.cookie_storage_path.exists())
        ):
            self.logger.info(
                "login_cookies enabled without stored state; "
                "forcing browser_headless=False for manual login."
            )
            self.browser_headless = False

        # Handle cookies
        if cookies:
            self._set_cookies(cookies)
            self.logger.info("Using provided cookies")
        elif auto_fetch_cookies:
            self._init_session(
                use_browser=use_browser_cookies,
                require_login=self.login_cookies,
                browser_headless=self.browser_headless,
                login_timeout=self.login_timeout,
                storage_state_path=self.cookie_storage_path,
            )
        else:
            self.logger.info("Skipping cookie initialization")

        self.parser = WeiboParser()

        self.proxy_pool = ProxyPool(config=proxy_config)
        self.rate_limit = rate_limit_config or RateLimitConfig()
        self.downloader = ImageDownloader(
            session=self.session,
            download_dir="./weibo_images",
            proxy_pool=self.proxy_pool,
        )
        self.video_downloader = VideoDownloader(
            session=self.session,
            download_dir="./weibo_videos",
            proxy_pool=self.proxy_pool,
        )

        if proxy_config and proxy_config.proxy_api_url:
            proxy_mode = "one-time" if proxy_config.use_once_proxy else "pooling"
            self.logger.info(
                f"Proxy enabled in {proxy_mode} mode "
                f"(API: {proxy_config.proxy_api_url}"
                + (
                    ""
                    if proxy_config.use_once_proxy
                    else f", Capacity: {proxy_config.pool_size}, "
                    f"TTL: {proxy_config.dynamic_proxy_ttl}s, "
                    f"Strategy: {proxy_config.fetch_strategy}"
                )
                + ")"
            )

        self.logger.info("WeiboClient initialized successfully")

    def _set_cookies(self, cookies: str | dict[str, str]):
        if isinstance(cookies, str):
            cookie_dict = {}
            for pair in cookies.split(";"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    cookie_dict[key.strip()] = value.strip()
            self.session.cookies.update(cookie_dict)
        elif isinstance(cookies, dict):
            self.session.cookies.update(cookies)

    def _has_login_cookies(self) -> bool:
        return any(self.session.cookies.get(name) for name in LOGIN_COOKIE_NAMES)

    def _init_session(
        self,
        use_browser: bool = False,
        require_login: bool = False,
        browser_headless: bool = True,
        login_timeout: int = 120,
        storage_state_path: str | Path | None = None,
    ):
        """
        Initialize session and fetch cookies

        Args:
            use_browser: If True, uses Playwright browser automation.
                If False, uses simple requests method.
            require_login: If True, waits for logged-in cookies to appear.
            browser_headless: Whether to run Playwright in headless mode.
            login_timeout: Timeout for manual login in seconds.
            storage_state_path: Optional path to persist Playwright storage state.
        """
        try:
            if require_login and not use_browser:
                self.logger.info(
                    "require_login=True requires browser cookies. "
                    "Forcing use_browser=True."
                )
                use_browser = True

            storage_state = (
                Path(storage_state_path).expanduser()
                if storage_state_path is not None
                else None
            )

            if (
                require_login
                and browser_headless
                and not (storage_state and storage_state.exists())
            ):
                self.logger.info(
                    "require_login enabled without stored state; "
                    "forcing browser_headless=False for manual login."
                )
                browser_headless = False

            method = "browser" if use_browser else "requests"
            self.logger.debug(f"Initializing session with {method} method...")

            if require_login:
                self.logger.info(
                    "Login cookies enabled. "
                    f"Please complete login within {login_timeout} seconds."
                )
                if storage_state:
                    self.logger.info(f"Cookie storage: {storage_state}")
                    if storage_state.exists():
                        self.logger.info("Using existing login storage state.")

            fetcher = CookieFetcher(
                user_agent=self.user_agent,
                use_browser=use_browser,
                require_login=require_login,
                login_timeout=login_timeout,
                headless=browser_headless,
                storage_state_path=storage_state,
            )
            cookies = fetcher.fetch_cookies()

            if cookies:
                self.session.cookies.update(cookies)
                self.logger.info(
                    f"Successfully fetched {len(cookies)} cookies using "
                    f"{'browser' if use_browser else 'requests'} method"
                )
            else:
                self.logger.warning("No cookies fetched during session initialization")

        except ImportError as e:
            if use_browser:
                # Browser mode is required, provide installation instructions
                self.logger.error(
                    "Playwright is required but not installed. "
                    "Please install it with the following commands:"
                )
                print("\n" + "=" * 70)
                print("ERROR: Playwright is not installed")
                print("=" * 70)
                print("\nWeibo's anti-scraping has been strengthened.")
                print("Browser automation is now required to fetch cookies.\n")
                print("Please run the following commands to install:\n")
                print("  uv add playwright")
                print("  uv run playwright install chromium\n")
                print("Or if you're using pip:\n")
                print("  pip install playwright")
                print("  playwright install chromium")
                print("=" * 70 + "\n")
                raise
            else:
                # Simple mode with ImportError is unexpected
                self.logger.error(f"Failed to initialize session: {e}")
                raise
        except Exception as e:
            # Catch Playwright browser not installed errors
            playwright_error = "playwright" in str(e).lower()
            executable_error = "executable" in str(e).lower()
            browser_error = "browser" in str(e).lower()

            if use_browser and (playwright_error or executable_error or browser_error):
                self.logger.error(
                    "Playwright browser is not installed. "
                    "Please install it with: playwright install chromium"
                )
                print("\n" + "=" * 70)
                print("ERROR: Playwright browser is not installed")
                print("=" * 70)
                print(
                    "\nPlaywright is installed, but the Chromium browser is missing.\n"
                )
                print("Please run the following command to install the browser:\n")
                print("  uv run playwright install chromium\n")
                print("Or if you're using pip:\n")
                print("  playwright install chromium")
                print("=" * 70 + "\n")
                raise
            else:
                self.logger.warning(f"Session initialization failed: {e}")

    def _request(
        self,
        url: str,
        params: dict[str, Any],
        max_retries: int = 3,
        use_proxy: bool = True,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Send HTTP request

        Args:
            url: Request URL
            params: Request parameters
            max_retries: Maximum number of retries
            use_proxy: Whether to use proxy, default True. Set to False to
                disable proxy for a single request
            headers: Optional headers to override per request

        Returns:
            Response JSON data
        """
        is_once_proxy = (
            use_proxy
            and self.proxy_pool
            and self.proxy_pool.is_enabled()
            and self.proxy_pool.config.use_once_proxy
        )

        for attempt in range(1, max_retries + 1):
            proxies = None
            using_proxy = False
            proxy_url = None
            if use_proxy and self.proxy_pool and self.proxy_pool.is_enabled():
                proxies = self.proxy_pool.get_proxy()
                if proxies:
                    using_proxy = True
                    proxy_url = proxies.get("http")
                    self.logger.debug(f"Using proxy: {proxy_url}")
                else:
                    self.logger.warning(
                        "Proxy pool failed to get available proxy, "
                        "request will proceed without proxy"
                    )

            try:
                request_headers = None
                if headers:
                    request_headers = dict(self.session.headers)
                    request_headers.update(headers)

                response = self.session.get(
                    url,
                    params=params,
                    proxies=proxies,
                    timeout=5,
                    headers=request_headers,
                )

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 432:
                    if using_proxy and proxy_url and not is_once_proxy:
                        if self.proxy_pool.remove_proxy(proxy_url):
                            self.logger.warning(
                                f"Proxy {proxy_url} returned 432, removed from pool"
                            )
                        else:
                            self.logger.debug(
                                f"Failed to remove proxy {proxy_url} from pool"
                            )

                    if attempt < max_retries:
                        if is_once_proxy:
                            self.logger.warning(
                                "Encountered 432 error with one-time proxy, "
                                "retrying immediately with fresh IP..."
                            )
                            continue
                        elif using_proxy:
                            sleep_time = random.uniform(0.5, 1.5)
                        else:
                            sleep_time = random.uniform(4, 7)
                        self.logger.warning(
                            f"Encountered 432 error, waiting {sleep_time:.1f} "
                            "seconds before retry..."
                        )
                        time.sleep(sleep_time)
                        continue
                    else:
                        raise NetworkError(
                            "Encountered 432 anti-crawler block", code="BLOCKED_432"
                        )
                else:
                    response.raise_for_status()

            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    if is_once_proxy:
                        self.logger.warning(
                            f"Request failed with one-time proxy, "
                            f"retrying immediately with fresh IP: {e}"
                        )
                        continue
                    elif using_proxy:
                        sleep_time = random.uniform(0.5, 1.5)
                    else:
                        sleep_time = random.uniform(2, 5)
                    self.logger.warning(
                        f"Request failed, waiting {sleep_time:.1f} seconds "
                        f"before retry: {e}"
                    )
                    time.sleep(sleep_time)
                    continue
                else:
                    code = (
                        "TIMEOUT"
                        if isinstance(e, requests.exceptions.Timeout)
                        else "NETWORK_ERROR"
                    )
                    raise NetworkError(f"Request failed: {e}", code=code)

        raise CrawlError("Maximum retry attempts reached")

    def refresh_cookies(
        self,
        use_browser: bool = False,
        login_cookies: bool = False,
        cookie_storage_path: str | Path | None = None,
        browser_headless: bool = True,
        login_timeout: int = 120,
    ):
        """
        Manually refresh cookies

        Args:
            use_browser: If True, uses Playwright browser automation.
                If False, uses simple requests method.
            login_cookies: If True, waits for logged-in cookies to appear.
            cookie_storage_path: Optional path to persist Playwright storage state.
            browser_headless: Whether to run Playwright in headless mode.
            login_timeout: Timeout for manual login in seconds.

        Raises:
            ImportError: If use_browser=True but playwright is not installed
        """
        if login_cookies and cookie_storage_path is None:
            cookie_storage_path = self.cookie_storage_path
        self._init_session(
            use_browser=use_browser,
            require_login=login_cookies,
            browser_headless=browser_headless,
            login_timeout=login_timeout,
            storage_state_path=cookie_storage_path,
        )

    def add_proxy(self, proxy_url: str, ttl: int | None = None):
        """
        Manually add static proxy to proxy pool

        Args:
            proxy_url: Proxy URL, format like 'http://1.2.3.4:8080' or 'http://user:pass@ip:port'
            ttl: Expiration time (seconds), None means never expires
        """
        self.proxy_pool.add_proxy(proxy_url, ttl)
        ttl_str = "never expires" if ttl is None else f"{ttl}s"
        self.logger.info(f"Added proxy to pool: {proxy_url}, TTL: {ttl_str}")

    def get_proxy_pool_size(self) -> int:
        """
        Get current proxy pool size

        Returns:
            Number of available proxies
        """
        return self.proxy_pool.get_pool_size()

    def clear_proxy_pool(self):
        """Clear proxy pool"""
        self.proxy_pool.clear_pool()
        self.logger.info("Proxy pool cleared")

    def _is_empty_value(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, bool):
            return False
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, dict, set, tuple)):
            return len(value) == 0
        if isinstance(value, (int, float)):
            return value == 0
        return False

    def _merge_user_info(
        self, base_info: dict[str, Any], extra_info: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(base_info)
        for key, value in extra_info.items():
            if key not in merged:
                if not self._is_empty_value(value):
                    merged[key] = value
                continue
            if self._is_empty_value(merged[key]) and not self._is_empty_value(value):
                merged[key] = value
                continue
            if isinstance(merged[key], bool) and merged[key] is False and value is True:
                merged[key] = True
        return merged

    def _fetch_profile_detail(self, uid: str, use_proxy: bool = True) -> dict[str, Any]:
        url = "https://weibo.com/ajax/profile/detail"
        headers = {"Referer": f"https://weibo.com/u/{uid}"}
        data = self._request(url, {"uid": uid}, use_proxy=use_proxy, headers=headers)
        return self.parser.parse_profile_detail(data)

    @rate_limit()
    def get_user_by_uid(self, uid: str, use_proxy: bool = True) -> User:
        """
        Get user information

        Args:
            uid: User ID
            use_proxy: Whether to use proxy, default True

        Returns:
            User object
        """
        url = "https://m.weibo.cn/api/container/getIndex"
        params = {"containerid": f"100505{uid}"}

        data = self._request(url, params, use_proxy=use_proxy)

        if not data.get("data") or not data["data"].get("userInfo"):
            raise UserNotFoundError(f"User {uid} not found")

        user_info = self.parser.parse_user_info(data)

        if self._has_login_cookies():
            try:
                detail_info = self._fetch_profile_detail(uid, use_proxy=use_proxy)
                user_info = self._merge_user_info(user_info, detail_info)
            except (ParseError, NetworkError) as e:
                self.logger.warning(
                    f"Failed to enrich user {uid} with profile detail: {e}"
                )
        user = User.from_dict(user_info)

        self.logger.info(f"Fetched user: {user.screen_name}")
        return user

    @rate_limit()
    def get_user_posts(
        self,
        uid: str,
        page: int = 1,
        expand: bool = False,
        with_comments: bool = False,
        comment_limit: int = 10,
        use_proxy: bool = True,
    ) -> list[Post]:
        """
        Get user's posts list

        Args:
            uid: User ID
            page: Page number
            expand: Whether to expand long text posts
            with_comments: If True, fetch comments for each post
            comment_limit: Maximum comments per post (default: 10)
            use_proxy: Whether to use proxy, default True

        Returns:
            List of Post objects (with comments if with_comments=True)
        """
        url = "https://m.weibo.cn/api/container/getIndex"
        params = {"containerid": f"107603{uid}", "page": page}

        data = self._request(url, params, use_proxy=use_proxy)

        if not data.get("data"):
            return []

        posts_data, pagination = self.parser.parse_posts(data)
        posts = [Post.from_dict(post_data) for post_data in posts_data]
        for post in posts:
            if post.is_long_text and expand:
                try:
                    long_post = self.get_post_by_bid(post.bid)
                    post.text = long_post.text
                    post.pic_urls = long_post.pic_urls
                    post.video_url = long_post.video_url
                    post.video_urls = long_post.video_urls
                except Exception as e:
                    self.logger.warning(f"Failed to expand long post {post.bid}: {e}")

        # Fetch comments if requested
        if with_comments and posts:
            posts = self._fetch_comments_for_posts(
                posts, comment_limit=comment_limit, use_proxy=use_proxy
            )

        self.logger.info(f"Fetched {len(posts)} posts")
        return posts

    def get_post_by_bid(
        self,
        bid: str,
        with_comments: bool = False,
        comment_limit: int = 10,
        use_proxy: bool = True,
    ) -> Post:
        """
        Get post details by bid

        Args:
            bid: Post bid
            with_comments: If True, fetch comments for the post
            comment_limit: Maximum comments to fetch (default: 10)
            use_proxy: Whether to use proxy, default True

        Returns:
            Post object (with comments if with_comments=True)
        """
        url = "https://m.weibo.cn/statuses/show"
        params = {"id": bid}

        data = self._request(url, params, use_proxy=use_proxy)

        if not data.get("data"):
            raise ParseError(f"Post {bid} not found")

        post_data = self.parser._parse_single_post(data["data"])
        if not post_data:
            raise ParseError(f"Failed to parse post data {bid}")

        post = Post.from_dict(post_data)

        # Fetch comments if requested
        if with_comments:
            try:
                max_pages = max(1, (comment_limit + 9) // 10)
                comments = self.get_all_comments(
                    post.id, max_pages=max_pages, use_proxy=use_proxy
                )
                post.comments = comments[:comment_limit]
            except Exception as e:
                self.logger.warning(f"Failed to fetch comments: {e}")
                post.comments = []

        return post

    @rate_limit()
    def search_users(
        self,
        query: str,
        page: int = 1,
        count: int = 10,
        use_proxy: bool = True,
        *,
        gender: str | None = None,
        location: str | None = None,
        birthday: str | None = None,
        age_range: tuple[int | None, int | None] | None = None,
        education: str | None = None,
        company: str | None = None,
    ) -> list[User]:
        """
        Search for users

        Args:
            query: Search keyword
            page: Page number
            count: Number of results per page
            use_proxy: Whether to use proxy, default True
            gender: Filter by gender (e.g., "m", "f", "male", "female")
            location: Filter by location (substring match)
            birthday: Filter by birthday (substring match)
            age_range: Filter by age range (min_age, max_age)
            education: Filter by education (substring match)
            company: Filter by company (substring match)

        Returns:
            List of User objects

        Note:
            Filters are applied locally based on fields returned by the search API.
        """
        url = "https://m.weibo.cn/api/container/getIndex"
        params = {
            "containerid": f"100103type=3&q={query}",
            "page": page,
            "count": count,
        }

        data = self._request(url, params, use_proxy=use_proxy)
        users = []
        cards = data.get("data", {}).get("cards", [])

        for card in cards:
            if card.get("card_type") == 11:
                card_group = card.get("card_group", [])
                for group_card in card_group:
                    if group_card.get("card_type") == 10:
                        user_data = group_card.get("user", {})
                        if user_data:
                            users.append(User.from_dict(user_data))

        total_users = len(users)
        filters_present = any(
            [
                gender,
                location,
                birthday,
                age_range,
                education,
                company,
            ]
        )

        if filters_present:
            users = filter_users(
                users,
                gender=gender,
                location=location,
                birthday=birthday,
                age_range=age_range,
                education=education,
                company=company,
            )
            self.logger.info(f"Found {total_users} users, {len(users)} after filtering")
        else:
            self.logger.info(f"Found {total_users} users")

        return users

    @rate_limit()
    def search_posts(
        self,
        query: str,
        page: int = 1,
        with_comments: bool = False,
        comment_limit: int = 10,
        use_proxy: bool = True,
    ) -> tuple[list[Post], dict[str, Any]]:
        """
        Search for posts

        Args:
            query: Search keyword
            page: Page number
            with_comments: If True, fetch comments for each post
            comment_limit: Maximum comments per post (default: 10)
            use_proxy: Whether to use proxy, default True

        Returns:
            Tuple of (List of Post objects, pagination info dict)
            Pagination info contains:
            - page: next page number (None if last page)
            - has_more: whether there are more pages
        """
        url = "https://m.weibo.cn/api/container/getIndex"
        params = {"containerid": f"100103type=1&q={query}", "page": page}

        data = self._request(url, params, use_proxy=use_proxy)
        posts_data, pagination = self.parser.parse_posts(data)
        posts = [Post.from_dict(post_data) for post_data in posts_data]

        # Fetch comments if requested
        if with_comments and posts:
            posts = self._fetch_comments_for_posts(
                posts, comment_limit=comment_limit, use_proxy=use_proxy
            )

        self.logger.info(
            f"Found {len(posts)} posts (has_more: {pagination.get('has_more', False)})"
        )
        return posts, pagination

    def _classify_page_failure(self, error: Exception) -> PageFailureReason:
        """
        Classify a page-level exception into a PageFailureReason so callers
        can decide whether to re-run failed pages

        Args:
            error: Exception raised while fetching a single page

        Returns:
            Classified failure reason
        """
        if isinstance(error, ParseError):
            return PageFailureReason.EMPTY_PARSE
        if isinstance(error, NetworkError):
            if error.code == "BLOCKED_432" or "432" in str(error):
                return PageFailureReason.BLOCKED_432
            if error.code == "TIMEOUT":
                return PageFailureReason.TIMEOUT
            return PageFailureReason.NETWORK_ERROR
        return PageFailureReason.UNKNOWN

    def _collect_paginated(
        self,
        fetch_page: Callable[[int], tuple[list[Any], bool]],
        max_pages: int | None = None,
        target_count: int | None = None,
        max_consecutive_failures: int = 5,
    ) -> BatchResult[Any]:
        """
        Generic batch pagination engine. Fetches pages one by one, keeping
        all successfully collected items even when individual pages fail.
        A failed page is recorded in the result and pagination continues
        with the next page instead of aborting the whole batch.

        Args:
            fetch_page: Callable taking a 1-based page number and returning
                (items, exhausted) where exhausted=True means the last page
                was reached
            max_pages: Maximum number of pages to fetch (None for unlimited)
            target_count: Stop once at least this many items were collected
                (None for no target)
            max_consecutive_failures: Abort pagination after this many
                consecutive failed pages (safety net against hammering the
                API when every page fails)

        Returns:
            BatchResult with collected items and per-page outcome details
        """
        items: list[Any] = []
        failed_pages: list[PageFailure] = []
        success_pages = 0
        consecutive_failures = 0
        page = 1
        stop_reason = "exhausted"

        while True:
            if max_pages and page > max_pages:
                stop_reason = "max_pages"
                break
            if target_count is not None and len(items) >= target_count:
                stop_reason = "count_reached"
                break

            try:
                page_items, exhausted = fetch_page(page)
            except Exception as e:
                reason = self._classify_page_failure(e)
                failed_pages.append(
                    PageFailure(page=page, reason=reason, message=str(e))
                )
                consecutive_failures += 1
                self.logger.error(
                    f"Page {page} failed ({reason.value}): {e}. "
                    "Continuing with next page."
                )
                if consecutive_failures >= max_consecutive_failures:
                    stop_reason = "max_consecutive_failures"
                    self.logger.warning(
                        f"Aborting pagination after {consecutive_failures} "
                        "consecutive failed pages"
                    )
                    break
                page += 1
                continue

            consecutive_failures = 0

            if not page_items:
                stop_reason = "exhausted"
                break

            items.extend(page_items)
            success_pages += 1

            if exhausted:
                stop_reason = "exhausted"
                break

            page += 1

        return BatchResult(
            items=items,
            success_pages=success_pages,
            failed_pages=failed_pages,
            stop_reason=stop_reason,
        )

    def search_posts_by_count(
        self,
        query: str,
        count: int,
        max_pages: int = 50,
        with_comments: bool = False,
        comment_limit: int = 10,
        use_proxy: bool = True,
    ) -> list[Post]:
        """
        Search for posts by keyword with automatic pagination until
        reaching specified count

        Args:
            query: Search keyword
            count: Desired number of posts to retrieve
            max_pages: Maximum number of pages to fetch (safety limit),
                default 50
            with_comments: If True, fetch comments for each post
            comment_limit: Maximum comments per post (default: 10)
            use_proxy: Whether to use proxy, default True

        Returns:
            List of Post objects (may be fewer than count if no more
            results available). Successfully fetched posts are preserved
            even if some pages fail.
        """
        return self.search_posts_by_count_with_result(
            query,
            count,
            max_pages=max_pages,
            with_comments=with_comments,
            comment_limit=comment_limit,
            use_proxy=use_proxy,
        ).items

    def search_posts_by_count_with_result(
        self,
        query: str,
        count: int,
        max_pages: int = 50,
        with_comments: bool = False,
        comment_limit: int = 10,
        use_proxy: bool = True,
    ) -> BatchResult[Post]:
        """
        Search for posts by keyword with automatic pagination until
        reaching specified count, returning a structured batch result

        Args:
            query: Search keyword
            count: Desired number of posts to retrieve
            max_pages: Maximum number of pages to fetch (safety limit),
                default 50
            with_comments: If True, fetch comments for each post
            comment_limit: Maximum comments per post (default: 10)
            use_proxy: Whether to use proxy, default True

        Returns:
            BatchResult containing all successfully fetched Post objects
            (truncated to count) plus per-page outcome details: successful
            page count, failed pages with classified reasons, and whether
            pagination ended normally (count reached, max_pages reached or
            data exhausted)
        """
        self.logger.info(
            f"Starting search for '{query}', target count: {count}, "
            f"max pages: {max_pages}"
        )

        def fetch_page(page: int) -> tuple[list[Post], bool]:
            posts, pagination = self.search_posts(
                query,
                page=page,
                with_comments=with_comments,
                comment_limit=comment_limit,
                use_proxy=use_proxy,
            )
            if not posts and not pagination:
                raise ParseError(f"Empty or malformed search response at page {page}")
            return posts, not pagination.get("has_more", False)

        result = self._collect_paginated(
            fetch_page, max_pages=max_pages, target_count=count
        )
        result.items = result.items[:count]

        self.logger.info(
            f"Search completed for '{query}': returned {len(result.items)} posts "
            f"({result.success_pages} pages ok, {result.failed_page_count} "
            f"pages failed, stop: {result.stop_reason})"
        )
        return result

    def search_all_posts(
        self,
        query: str,
        max_pages: int | None = None,
        with_comments: bool = False,
        comment_limit: int = 10,
        use_proxy: bool = True,
    ) -> list[Post]:
        """
        Search for all posts by keyword with automatic pagination until
        reaching the last page (detected by cardlistInfo.page being None)

        Args:
            query: Search keyword
            max_pages: Maximum number of pages to fetch (safety limit),
                None for unlimited
            with_comments: If True, fetch comments for each post
            comment_limit: Maximum comments per post (default: 10)
            use_proxy: Whether to use proxy, default True

        Returns:
            List of all available Post objects. Successfully fetched posts
            are preserved even if some pages fail.
        """
        return self.search_all_posts_with_result(
            query,
            max_pages=max_pages,
            with_comments=with_comments,
            comment_limit=comment_limit,
            use_proxy=use_proxy,
        ).items

    def search_all_posts_with_result(
        self,
        query: str,
        max_pages: int | None = None,
        with_comments: bool = False,
        comment_limit: int = 10,
        use_proxy: bool = True,
    ) -> BatchResult[Post]:
        """
        Search for all posts by keyword with automatic pagination until
        reaching the last page (detected by cardlistInfo.page being None),
        returning a structured batch result

        Args:
            query: Search keyword
            max_pages: Maximum number of pages to fetch (safety limit),
                None for unlimited
            with_comments: If True, fetch comments for each post
            comment_limit: Maximum comments per post (default: 10)
            use_proxy: Whether to use proxy, default True

        Returns:
            BatchResult containing all successfully fetched Post objects
            plus per-page outcome details: successful page count, failed
            pages with classified reasons, and whether pagination ended
            normally (max_pages reached or data exhausted)
        """
        self.logger.info(
            f"Starting search for all posts matching '{query}'"
            + (f", max pages: {max_pages}" if max_pages else " (no limit)")
        )

        def fetch_page(page: int) -> tuple[list[Post], bool]:
            posts, pagination = self.search_posts(
                query,
                page=page,
                with_comments=with_comments,
                comment_limit=comment_limit,
                use_proxy=use_proxy,
            )
            if not posts and not pagination:
                raise ParseError(f"Empty or malformed search response at page {page}")
            return posts, not pagination.get("has_more", False)

        result = self._collect_paginated(fetch_page, max_pages=max_pages)

        self.logger.info(
            f"Search completed for '{query}': fetched {len(result.items)} posts "
            f"({result.success_pages} pages ok, {result.failed_page_count} "
            f"pages failed, stop: {result.stop_reason})"
        )
        return result

    def download_post_images(
        self,
        post: Post,
        download_dir: str | None = None,
        subdir: str | None = None,
    ) -> dict[str, str | None]:
        """
        Download images from a single post

        Args:
            post: Post object containing image URLs
            download_dir: Custom download directory (optional)
            subdir: Subdirectory name for organizing downloads

        Returns:
            Dictionary mapping image URLs to downloaded file paths
        """
        if download_dir:
            self.downloader.download_dir = Path(download_dir)
            self.downloader.download_dir.mkdir(parents=True, exist_ok=True)

        if not post.pic_urls:
            self.logger.info(f"Post {post.id} has no images to download")
            return {}

        return self.downloader.download_post_images(post.pic_urls, post.id, subdir)

    def download_posts_images(
        self,
        posts: list[Post],
        download_dir: str | None = None,
        subdir: str | None = None,
    ) -> dict[str, dict[str, str | None]]:
        """
        Download images from multiple posts

        Args:
            posts: List of Post objects
            download_dir: Custom download directory (optional)
            subdir: Subdirectory name for organizing downloads

        Returns:
            Dictionary mapping post IDs to their download results
        """
        if download_dir:
            self.downloader.download_dir = Path(download_dir)
            self.downloader.download_dir.mkdir(parents=True, exist_ok=True)

        posts_with_images = [post for post in posts if post.pic_urls]
        if not posts_with_images:
            self.logger.info("No posts with images found")
            return {}

        self.logger.info(
            f"Found {len(posts_with_images)} posts with images "
            f"out of {len(posts)} total posts"
        )
        return self.downloader.download_posts_images(posts_with_images, subdir)

    def download_user_posts_images(
        self,
        uid: str,
        pages: int = 1,
        download_dir: str | None = None,
        expand_long_text: bool = False,
    ) -> dict[str, dict[str, str | None]]:
        """
        Download images from user's posts

        Args:
            uid: User ID
            pages: Number of pages to fetch
            download_dir: Custom download directory (optional)
            expand_long_text: Whether to expand long text posts

        Returns:
            Dictionary mapping post IDs to their download results
        """
        all_posts = []

        for page in range(1, pages + 1):
            posts = self.get_user_posts(uid, page=page, expand=expand_long_text)
            if not posts:
                break
            all_posts.extend(posts)

        subdir = f"user_{uid}"

        return self.download_posts_images(all_posts, download_dir, subdir)

    def download_post_video(
        self,
        post: Post,
        download_dir: str | None = None,
        subdir: str | None = None,
    ) -> str | None:
        """
        Download video from a single post

        Args:
            post: Post object containing video URL
            download_dir: Custom download directory (optional)
            subdir: Subdirectory name for organizing downloads

        Returns:
            Path to downloaded file if successful, None otherwise
        """
        if download_dir:
            self.video_downloader.download_dir = Path(download_dir)
            self.video_downloader.download_dir.mkdir(parents=True, exist_ok=True)

        if not post.video_url:
            self.logger.info(f"Post {post.id} has no video to download")
            return None

        return self.video_downloader.download_post_video(post, subdir)

    def download_posts_videos(
        self,
        posts: list[Post],
        download_dir: str | None = None,
        subdir: str | None = None,
    ) -> dict[str, str | None]:
        """
        Download videos from multiple posts

        Args:
            posts: List of Post objects
            download_dir: Custom download directory (optional)
            subdir: Subdirectory name for organizing downloads

        Returns:
            Dictionary mapping post IDs to downloaded file paths
        """
        if download_dir:
            self.video_downloader.download_dir = Path(download_dir)
            self.video_downloader.download_dir.mkdir(parents=True, exist_ok=True)

        posts_with_video = [post for post in posts if post.video_url]
        if not posts_with_video:
            self.logger.info("No posts with videos found")
            return {}

        self.logger.info(
            f"Found {len(posts_with_video)} posts with videos "
            f"out of {len(posts)} total posts"
        )
        return self.video_downloader.download_posts_videos(posts_with_video, subdir)

    def download_user_posts_videos(
        self,
        uid: str,
        pages: int = 1,
        download_dir: str | None = None,
        expand_long_text: bool = False,
    ) -> dict[str, str | None]:
        """
        Download videos from user's posts

        Args:
            uid: User ID
            pages: Number of pages to fetch
            download_dir: Custom download directory (optional)
            expand_long_text: Whether to expand long text posts

        Returns:
            Dictionary mapping post IDs to downloaded file paths
        """
        all_posts: list[Post] = []

        for page in range(1, pages + 1):
            posts = self.get_user_posts(uid, page=page, expand=expand_long_text)
            if not posts:
                break
            all_posts.extend(posts)

        subdir = f"user_{uid}"

        return self.download_posts_videos(all_posts, download_dir, subdir)

    @rate_limit()
    def get_comments(
        self,
        post_id: str,
        page: int = 1,
        use_proxy: bool = True,
    ) -> tuple[list[Comment], dict[str, int]]:
        """
        Get comments for a specific post

        Args:
            post_id: Post ID (numeric ID, not bid)
            page: Page number
            use_proxy: Whether to use proxy, default True

        Returns:
            Tuple of (List of Comment objects, pagination info dict with
            total_number and max fields)
        """
        url = "https://m.weibo.cn/api/comments/show"
        params = {"id": post_id, "page": page}

        data = self._request(url, params, use_proxy=use_proxy)

        if not data.get("data"):
            return [], {"total_number": 0, "max": 0}

        comments_data, pagination = self.parser.parse_comments(data)
        comments = [Comment.from_dict(comment_data) for comment_data in comments_data]

        self.logger.info(
            f"Fetched {len(comments)} comments for post {post_id} "
            f"(page {page}, total: {pagination.get('total_number', 0)})"
        )
        return comments, pagination

    def get_all_comments(
        self,
        post_id: str,
        max_pages: int | None = None,
        use_proxy: bool = True,
    ) -> list[Comment]:
        """
        Get all comments for a specific post with automatic pagination

        Args:
            post_id: Post ID (numeric ID, not bid)
            max_pages: Maximum number of pages to fetch (None for all pages)
            use_proxy: Whether to use proxy, default True

        Returns:
            List of all Comment objects. Successfully fetched comments are
            preserved even if some pages fail.
        """
        return self.get_all_comments_with_result(
            post_id, max_pages=max_pages, use_proxy=use_proxy
        ).items

    def get_all_comments_with_result(
        self,
        post_id: str,
        max_pages: int | None = None,
        use_proxy: bool = True,
    ) -> BatchResult[Comment]:
        """
        Get all comments for a specific post with automatic pagination,
        returning a structured batch result

        Args:
            post_id: Post ID (numeric ID, not bid)
            max_pages: Maximum number of pages to fetch (None for all pages)
            use_proxy: Whether to use proxy, default True

        Returns:
            BatchResult containing all successfully fetched Comment objects
            plus per-page outcome details: successful page count, failed
            pages with classified reasons, and whether pagination ended
            normally (max_pages reached or data exhausted)
        """
        self.logger.info(
            f"Starting to fetch all comments for post {post_id}"
            + (f", max pages: {max_pages}" if max_pages else "")
        )

        def fetch_page(page: int) -> tuple[list[Comment], bool]:
            comments, pagination = self.get_comments(
                post_id, page=page, use_proxy=use_proxy
            )
            max_page = pagination.get("max", 0)
            exhausted = max_page > 0 and page >= max_page
            return comments, exhausted

        result = self._collect_paginated(fetch_page, max_pages=max_pages)

        self.logger.info(
            f"Fetched total {len(result.items)} comments "
            f"({result.success_pages} pages ok, {result.failed_page_count} "
            f"pages failed, stop: {result.stop_reason})"
        )
        return result

    def _fetch_comments_for_posts(
        self,
        posts: list[Post],
        comment_limit: int = 10,
        use_proxy: bool = True,
    ) -> list[Post]:
        """
        Fetch comments for multiple posts sequentially

        Args:
            posts: List of Post objects to fetch comments for
            comment_limit: Maximum number of comments per post (default: 10)
            use_proxy: Whether to use proxy for requests

        Returns:
            Same list of Post objects with comments populated
        """
        if not posts:
            return posts

        # Calculate pages needed (10 comments per page)
        max_pages = max(1, (comment_limit + 9) // 10)

        self.logger.info(
            f"Fetching comments for {len(posts)} posts "
            f"(limit: {comment_limit} per post)"
        )

        successful_count = 0
        for i, post in enumerate(posts, 1):
            try:
                comments = self.get_all_comments(
                    post.id, max_pages=max_pages, use_proxy=use_proxy
                )
                post.comments = comments[:comment_limit]
                if post.comments:
                    successful_count += 1
                self.logger.debug(
                    f"[{i}/{len(posts)}] Fetched {len(post.comments)} comments "
                    f"for post {post.id}"
                )
            except Exception as e:
                self.logger.warning(
                    f"[{i}/{len(posts)}] Failed to fetch comments "
                    f"for post {post.id}: {e}"
                )
                post.comments = []

        self.logger.info(
            f"Comment fetching complete: {successful_count}/{len(posts)} "
            f"posts have comments"
        )

        return posts
