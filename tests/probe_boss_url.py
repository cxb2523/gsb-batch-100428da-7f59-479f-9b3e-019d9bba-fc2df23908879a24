"""探测 BOSS 直聘企业端页面地址与跳转行为。
用法：python tests/probe_boss_url.py [URL]
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from config import find_edge
from engine.stealth import STEALTH_JS

URLS = [
    "https://www.zhipin.com/web/boss/recommend",
    "https://www.zhipin.com/web/boss/search",
    "https://www.zhipin.com/web/geek/job",
    "https://www.zhipin.com/",
]


def main():
    targets = [sys.argv[1]] if len(sys.argv) > 1 else URLS
    edge = find_edge()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="probe-boss-url-"),
            executable_path=edge,
            headless=True,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        ctx.add_init_script(STEALTH_JS)
        page = ctx.new_page()
        for url in targets:
            print(f"\n== {url} ==")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                for i in range(4):
                    page.wait_for_timeout(2000)
                    print(f"  t+{(i + 1) * 2}s: {page.url}")
                    if page.url == "about:blank":
                        break
                body = ""
                try:
                    body = page.locator("body").inner_text(timeout=3000)[:200].replace("\n", " ")
                except Exception:
                    pass
                print("  页面文本:", body)
            except Exception as e:
                print(f"  异常: {e}")
        ctx.close()


if __name__ == "__main__":
    main()
