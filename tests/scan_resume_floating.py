"""扫描真实 BOSS 沟通页的在线简历悬浮层结构（需应用已关闭、登录态可用）。
用法：python tests/scan_resume_floating.py
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
        time.sleep(4)
        print("当前 URL:", page.url)

        # 点开第一个候选人（含名字的元素）
        try:
            first = page.locator(".geek-name").first
            if first.count() > 0:
                first.click(timeout=5000)
                time.sleep(2)
                print("已点开候选人:", first.inner_text())
        except Exception as e:
            print("点候选人失败:", e)

        # 找「在线简历」并点击
        try:
            tabs = page.locator(":text-is('在线简历')")
            print("在线简历元素数量:", tabs.count())
            for i in range(tabs.count()):
                el = tabs.nth(i)
                try:
                    if el.is_visible(timeout=800):
                        el.click(timeout=3000)
                        print(f"已点击第 {i + 1} 个在线简历")
                        break
                except Exception:
                    continue
            time.sleep(3)
        except Exception as e:
            print("点击在线简历失败:", e)

        # 扫描悬浮层结构
        report = page.evaluate("""
            () => {
                const out = {frames: [], containers: [], imgs: [], scrollables: []};
                out.frames = [...document.querySelectorAll('iframe')].map(f => f.src || f.getAttribute('src'));
                const known = document.querySelectorAll(
                    '[class*="resume-recommend"], [class*="resume-common"], [class*="boss-dialog"], [class*="boss-popup"], [class*="resume-detail"], [class*="resume-panel"]'
                );
                known.forEach(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    out.containers.push({
                        cls: (el.className || '').toString().slice(0, 80),
                        x: Math.round(r.x), y: Math.round(r.y),
                        w: Math.round(r.width), h: Math.round(r.height),
                        display: s.display, visible: s.visibility,
                        textLen: (el.innerText || '').length,
                        imgs: el.querySelectorAll('img').length,
                        bg: (s.backgroundImage || '').slice(0, 60)
                    });
                });
                // 所有可见 img（记录 src 与尺寸）
                document.querySelectorAll('img').forEach(img => {
                    const r = img.getBoundingClientRect();
                    if (r.width > 50 && r.height > 50) {
                        out.imgs.push({
                            src: (img.src || '').slice(-80),
                            w: Math.round(r.width), h: Math.round(r.height),
                            x: Math.round(r.x), y: Math.round(r.y)
                        });
                    }
                });
                // 可滚动容器
                document.querySelectorAll('[class*="resume"], [class*="dialog"] *').forEach(el => {
                    if (el.scrollHeight > el.clientHeight + 20 && el.clientHeight > 50) {
                        const r = el.getBoundingClientRect();
                        out.scrollables.push({
                            cls: (el.className || '').toString().slice(0, 80),
                            scrollH: el.scrollHeight, clientH: el.clientHeight,
                            x: Math.round(r.x), y: Math.round(r.y),
                            w: Math.round(r.width), h: Math.round(r.height)
                        });
                    }
                });
                out.bodyLen = (document.body.innerText || '').length;
                return out;
            }
        """)
        print("\niframe:", report["frames"])
        print("已知简历容器:", report["containers"])
        print("大图 img:", report["imgs"][:10])
        print("可滚动容器:", report["scrollables"][:8])
        print("body 文本长度:", report["bodyLen"])

        # 截图整页与可能悬浮区域，供人工确认
        os.makedirs("data/resumes", exist_ok=True)
        page.screenshot(path="data/resumes/扫描_整页.png")
        print("\n整页截图已保存：data/resumes/扫描_整页.png")
        if report["containers"]:
            c = report["containers"][0]
            if c["w"] > 100 and c["h"] > 100:
                page.screenshot(path="data/resumes/扫描_悬浮层.png",
                                clip={"x": c["x"], "y": c["y"], "width": c["w"], "height": c["h"]})
                print("悬浮层截图已保存：data/resumes/扫描_悬浮层.png")

        ctx.close()


if __name__ == "__main__":
    main()
