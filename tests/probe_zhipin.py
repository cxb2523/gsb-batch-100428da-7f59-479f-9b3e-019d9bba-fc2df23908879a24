"""探测 zhipin.com 在 Playwright 环境下的跳转行为。
用法：python tests/probe_zhipin.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from config import find_edge
from engine.stealth import STEALTH_JS


def probe(mode="default", headless=True):
    edge = find_edge()
    args = []
    ignore = []
    init_scripts = []
    if mode in ("anti", "stealth"):
        # 去掉 Playwright 默认注入的 --enable-automation 开关（webdriver 特征来源）
        ignore.append("--enable-automation")
        init_scripts.append(STEALTH_JS)
    if mode == "stealth":
        args.extend(["--disable-blink-features=AutomationControlled"])
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="probe-zhipin-"),
            executable_path=edge,
            headless=headless,
            slow_mo=50,
            args=args,
            ignore_default_args=ignore,
        )
        for js in init_scripts:
            ctx.add_init_script(js)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://www.zhipin.com/", wait_until="domcontentloaded", timeout=45000)
            print(f"[{mode}] 打开后 URL: {page.url}")
            for i in range(5):
                page.wait_for_timeout(2000)
                print(f"    t+{(i + 1) * 2}s: {page.url}")
                if page.url == "about:blank":
                    print("    >>> 被跳回 about:blank（复现成功）")
                    break
        except Exception as e:
            print(f"[{mode}] 异常: {e}")
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if mode == "compare":
        print("== 默认启动 ==")
        probe()
        print("\n== 反检测1（去 --enable-automation + 特征伪装）==")
        probe("anti")
        print("\n== 反检测2（再加禁用自动化特征 flag）==")
        probe("stealth")
    elif mode == "headed":
        probe("stealth", headless=False)
    else:
        probe(mode)
