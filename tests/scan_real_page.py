"""扫描真实 BOSS 沟通页的按钮结构（需应用已关闭、edge_profile 登录态可用）。
用法：python tests/scan_real_page.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from config import EDGE_PROFILE_DIR, find_edge
from engine.stealth import STEALTH_JS


def main():
    edge = find_edge()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            EDGE_PROFILE_DIR,
            executable_path=edge,
            headless=True,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        ctx.add_init_script(STEALTH_JS)
        page = ctx.new_page()
        page.goto("https://www.zhipin.com/web/chat/index", wait_until="domcontentloaded", timeout=45000)
        time.sleep(5)
        print("当前 URL:", page.url)

        # 扫描按钮/链接文本
        texts = page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('button, a, [class*="tool"], [class*="btn"]').forEach(el => {
                    const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                    if (t && t.length <= 20) out.push(t);
                });
                return [...new Set(out)].slice(0, 80);
            }
        """)
        print("按钮/链接文本:", "、".join(texts))

        # 扫描带 title / aria-label 的元素（图标按钮的提示文字）
        titled = page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('[title], [aria-label]').forEach(el => {
                    const t = el.getAttribute('title') || el.getAttribute('aria-label');
                    if (t && t.trim()) {
                        out.push({
                            tag: el.tagName,
                            cls: (el.className || '').toString().slice(0, 100),
                            tip: t.trim().slice(0, 40),
                            html: el.outerHTML.slice(0, 200)
                        });
                    }
                });
                return out.slice(0, 40);
            }
        """)
        print("\n带 title/aria-label 的元素：")
        for i, el in enumerate(titled):
            print(f"  [{i}] <{el['tag']}> tip={el['tip']!r} class={el['cls']!r}")

        # 扫描聊天窗口工具条区域的所有按钮/图标
        chat_btns = page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('[class*="chat"] button, [class*="chat"] [class*="icon"], [class*="tool"] img').forEach(el => {
                    out.push({
                        tag: el.tagName,
                        cls: (el.className || '').toString().slice(0, 100),
                        tip: (el.getAttribute('title') || '').slice(0, 40),
                        text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 30)
                    });
                });
                return out.slice(0, 50);
            }
        """)
        print("\n聊天区域按钮/图标：")
        for i, el in enumerate(chat_btns):
            print(f"  [{i}] <{el['tag']}> class={el['cls']!r} tip={el['tip']!r} text={el['text']!r}")

        # 1) 悬停到第一个候选人行，看悬停图标
        try:
            first_geek = page.locator(".geek-name").first
            if first_geek.count() > 0:
                first_geek.hover(timeout=3000)
                time.sleep(1)
                icons = page.evaluate("""
                    () => [...document.querySelectorAll('.icon-operate')].slice(0, 12).map(el => ({
                        cls: (el.className || '').toString(),
                        src: (el.getAttribute('src') || '').slice(-60),
                        tip: (el.getAttribute('title') || '')
                    }))
                """)
                print("\n候选人行悬停图标：")
                for i, el in enumerate(icons):
                    print(f"  [{i}] class={el['cls']!r} src=...{el['src']!r} tip={el['tip']!r}")
        except Exception as e:
            print("悬停候选人失败:", e)

        # 2) 点击第一个 icon-operate，扫描弹出菜单
        try:
            op = page.locator(".icon-operate").first
            if op.count() > 0 and op.is_visible(timeout=2000):
                op.click(timeout=3000)
                time.sleep(1.5)
                menus = page.evaluate("""
                    () => [...new Set(
                        [...document.querySelectorAll('[class*="menu"] *, [class*="popup"] *, [class*="dropdown"] *, [class*="tooltip"] *')]
                            .map(el => (el.innerText || '').trim().replace(/\\s+/g, ' '))
                            .filter(t => t && t.length <= 20)
                    )].slice(0, 60)
                """)
                print("\n点击操作图标后菜单:", "、".join(menus))
                # 截取含「简历」的菜单项 html
                resume_menu = page.evaluate("""
                    () => [...document.querySelectorAll('[class*="menu"] *, [class*="popup"] *, [class*="dropdown"] *')]
                        .filter(el => /简历|求|索要/.test((el.innerText || '')))
                        .map(el => ({ text: (el.innerText || '').trim().replace(/\\s+/g,' ').slice(0,20),
                                      cls: (el.className || '').toString().slice(0,80),
                                      html: el.outerHTML.slice(0, 250) }))
                        .slice(0, 10)
                """)
                print("含「简历」的菜单项：")
                for el in resume_menu:
                    print(f"  text={el['text']!r} cls={el['cls']!r}")
                    print(f"    html: {el['html']}")
        except Exception as e:
            print("点击操作图标失败:", e)

        # 3) 点击第一个候选人，打开聊天窗口，扫描工具条
        try:
            first_geek = page.locator(".geek-name").first
            if first_geek.count() > 0:
                first_geek.click(timeout=3000)
                time.sleep(2)
                header_btns = page.evaluate("""
                    () => [...document.querySelectorAll('[class*="chat-header"] *, [class*="toolbar"] *, [class*="operate"] button')]
                        .filter(el => el.innerText && el.innerText.trim().length <= 16)
                        .map(el => ({ text: (el.innerText || '').trim().replace(/\\s+/g,' '),
                                      cls: (el.className || '').toString().slice(0,80) }))
                        .slice(0, 40)
                """)
                print("\n聊天窗口头部按钮:")
                for el in header_btns:
                    print(f"  text={el['text']!r} cls={el['cls']!r}")
        except Exception as e:
            print("打开聊天失败:", e)

        # 扫描含「简历」关键词的元素
        resume_els = page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('button, a, div, span, li').forEach(el => {
                    const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                    if (t && t.length <= 24 && /简历|求|索要/.test(t)) {
                        out.push({
                            tag: el.tagName,
                            cls: (el.className || '').toString().slice(0, 120),
                            text: t,
                            html: el.outerHTML.slice(0, 300)
                        });
                    }
                });
                return out.slice(0, 30);
            }
        """)
        print("\n含「简历/求/索要」的元素：")
        for i, el in enumerate(resume_els):
            print(f"  [{i}] <{el['tag']}> class={el['cls']!r} text={el['text']!r}")
            print(f"      html: {el['html']}")

        ctx.close()


if __name__ == "__main__":
    main()
