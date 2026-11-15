# -*- coding: utf-8 -*-
"""通过 CDP 接管用户已登录的 Chrome。

用 Playwright 的 connect_over_cdp 连接 --remote-debugging-port 起的实例，
不启动自带浏览器，从而复用真实登录态、指纹与 cookie。
"""
from typing import Optional
from config import CDP_URL, FANQIE_HOST


def connect(cdp_url: str = CDP_URL):
    """连接 CDP，返回 (playwright, browser, context)。失败抛异常并给出提示。"""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    try:
        browser = p.chromium.connect_over_cdp(cdp_url)
    except Exception as e:
        p.stop()
        raise RuntimeError(
            f'无法连接 CDP({cdp_url})。请先用 launch_chrome 脚本启动 Chrome，'
            f'确认调试端口已开启。原始错误: {e}')
    if not browser.contexts:
        p.stop()
        raise RuntimeError('已连接浏览器但没有可用上下文。')
    return p, browser, browser.contexts[0]


def find_fanqie_pages(context) -> list:
    """枚举当前已打开的标签页，返回停留在番茄站点的页面列表。"""
    return [pg for pg in context.pages if FANQIE_HOST in (pg.url or '')]


def get_or_open_page(context, url: Optional[str] = None):
    """复用番茄标签页；没有则新开一个。可选导航到 url。"""
    pages = find_fanqie_pages(context)
    page = pages[0] if pages else context.new_page()
    if url:
        page.goto(url, wait_until='domcontentloaded')
    return page


# 只在可见文本里判定的强验证信号。
# 不含单独的“验证码”——那会命中登录组件的“获取验证码”按钮，造成误报。
_VERIFY_MARKERS = ['滑动验证', '拖动滑块', '拖动下方滑块', '完成下方验证',
                   '人机验证', '安全验证', '请完成验证']

# 可见即代表被风控拦截的验证码容器（字节跳动验证码 SDK 常见结构）。
_CAPTCHA_VISIBLE_JS = """() => {
  const sels = [
    'iframe[src*="captcha"]',
    '[id*="captcha" i]',
    '[class*="captcha" i]',
    '[class*="verify-slider"]',
    '[class*="secsdk"]',
  ];
  return sels.some(s => {
    let el;
    try { el = document.querySelector(s); } catch (e) { return false; }
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;  // 必须可见
  });
}"""


def is_verify_page(page) -> bool:
    """粗判当前是否为风控/验证码页面。

    只看可见文本与可见的验证码 DOM 元素，避免匹配打包 JS 源码里
    incidental 的 verify/captcha 字样（那是 is_verify_page 误报的根因）。
    """
    try:
        if page.evaluate(_CAPTCHA_VISIBLE_JS):
            return True
        body_text = page.inner_text('body')[:3000]
    except Exception:  # noqa: BLE001
        return False
    return any(m in body_text for m in _VERIFY_MARKERS)


def wait_manual_verify(page, log=print):
    """交互模式（探针用）：检测到验证页时提示用户手动过码后按回车续爬。"""
    if is_verify_page(page):
        log('⚠ 检测到验证码/风控页，请在浏览器窗口手动完成验证...')
        input('>>> 完成验证后按 Enter 继续...')
        page.wait_for_timeout(1000)


def wait_verify_cleared(page, log=print, timeout_s: int = 600,
                        poll_s: float = 3.0) -> bool:
    """无人值守模式（下载器用）：轮询等验证页自动清除。

    不阻塞在 stdin 上——用户在浏览器窗口手动过码，脚本自动感知后继续。
    返回是否在超时前通过。
    """
    if not is_verify_page(page):
        return True
    log(f'⚠ 检测到验证码/风控页，请在浏览器窗口手动完成验证'
        f'（自动检测中，最长等待 {timeout_s}s）...')
    waited = 0.0
    while waited < timeout_s:
        page.wait_for_timeout(int(poll_s * 1000))
        waited += poll_s
        if not is_verify_page(page):
            log('✓ 验证已通过，继续抓取。')
            page.wait_for_timeout(1000)
            return True
    log(f'✗ 等待验证超时（{timeout_s}s），本次放弃该章。')
    return False

