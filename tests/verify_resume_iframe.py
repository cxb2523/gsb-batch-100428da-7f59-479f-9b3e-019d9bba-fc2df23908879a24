"""验证：在线简历 iframe 里能否直接抓到文字。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from config import EDGE_PROFILE_DIR, find_edge
from engine.stealth import STEALTH_JS


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            EDGE_PROFILE_DIR,
            executable_path=find_edge(),
            headless=True,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        ctx.add_init_script(STEALTH_JS)
        page = ctx.new_page()
        page.goto("https://www.zhipin.com/web/chat/index", wait_until="domcontentloaded", timeout=45000)
        time.sleep(4)

        first = page.locator(".geek-name").first
        first.click(timeout=5000)
        time.sleep(2)

        tab = page.locator(":text-is('在线简历')").first
        tab.click(timeout=3000)
        time.sleep(3)

        # 等待 iframe 出现并抓文本
        iframe_text = ""
        iframe_url = ""
        deadline = time.time() + 10
        while time.time() < deadline:
            for f in page.frames:
                if "c-resume" in f.url or "resume" in f.url.lower():
                    try:
                        t = f.locator("body").inner_text(timeout=1500)
                    except Exception:
                        continue
                    if t and len(t) > 50:
                        iframe_text = t
                        iframe_url = f.url
                        break
            if iframe_text:
                break
            time.sleep(1)

        print("iframe URL:", iframe_url)
        if iframe_text:
            print("iframe 文本长度:", len(iframe_text))
            print("前 800 字：")
            print(iframe_text[:800])
        else:
            print("⚠️ iframe 内未抓到文本（简历可能是图片）")
            # 输出 iframe 内 img
            for f in page.frames:
                if "resume" in f.url.lower():
                    try:
                        imgs = f.evaluate("""
                            () => [...document.querySelectorAll('img')].map(i => ({
                                src: (i.src || '').slice(-80),
                                w: Math.round(i.getBoundingClientRect().width),
                                h: Math.round(i.getBoundingClientRect().height)
                            })).slice(0, 10)
                        """)
                        print("iframe 内 img:", imgs)
                    except Exception as e:
                        print("iframe img 查询失败:", e)

        ctx.close()


if __name__ == "__main__":
    main()
