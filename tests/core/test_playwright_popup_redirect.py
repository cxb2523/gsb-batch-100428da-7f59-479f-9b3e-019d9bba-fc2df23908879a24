"""``target=_blank`` 同标签页改道的可观察性测试.

覆盖两条验收线:

1. 改道**成功后**, 紧随其后的截图/定位必须来自新地址, 而不是跳转前的
   旧文档(旧页面与新页面用不同的背景色 + 不同的 marker 文本区分)。
2. 改道**失败**(目标不可达)时, 后续采集不能把旧页面当成已到达的目标页:
   必须抛 ``PopupNavigationError`` 并留下可诊断的日志。

测试全部使用本地自造的页面(``data:`` URL 与本地 HTTP 服务), 不依赖外网。
"""

from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import io
import os
import pathlib
import socket
import threading
from typing import Any, Iterator

import pytest

pytest.importorskip("playwright.async_api")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from playwright.async_api import async_playwright  # noqa: E402

from pymidscene.web_integration.playwright.page import (  # noqa: E402
    PopupNavigationError,
    WebPage,
)

PIL = pytest.importorskip("PIL.Image")

OLD_HTML = """<!doctype html>
<html><head><title>OLD PAGE</title></head>
<body style="margin:0;background:#ffdddd">
  <h1 id="marker">OLD PAGE</h1>
  <p id="price">price: 111</p>
  <a id="open" href="{target}" target="_blank"
     style="display:block;width:400px;height:120px">open target</a>
</body></html>
"""

NEW_HTML = """<!doctype html>
<html><head><title>NEW PAGE</title></head>
<body style="margin:0;background:#ddffdd">
  <h1 id="marker">NEW PAGE</h1>
  <p id="price">price: 999</p>
</body></html>
"""


def _chromium_available() -> bool:
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and os.name == "nt":
        default = os.path.join(
            os.path.expanduser("~"), "AppData", "Local", "ms-playwright"
        )
    else:
        default = os.path.expanduser("~/.cache/ms-playwright")
    cache = pathlib.Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or default)
    return cache.exists() and any(cache.glob("chromium*"))


pytestmark = [
    pytest.mark.skipif(
        not _chromium_available(),
        reason="Chromium not installed — run `playwright install chromium`",
    ),
]


# ---------------------------------------------------------------------------
# 本地站点 (旧页 / 新页各用不同背景色, 便于从截图像素上判断来源)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def site() -> Iterator[dict[str, str]]:
    tmp = pathlib.Path(__file__).parent / "_popup_site"
    tmp.mkdir(exist_ok=True)
    (tmp / "new.html").write_text(NEW_HTML, encoding="utf-8")
    (tmp / "old.html").write_text(
        OLD_HTML.format(target="/new.html"), encoding="utf-8"
    )
    (tmp / "dead.html").write_text(
        OLD_HTML.format(target="http://127.0.0.1:{dead_port}/nope"),
        encoding="utf-8",
    )

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # noqa: D102
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(_Handler, directory=str(tmp)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    # 一个确定没人监听的端口, 用来制造"目标不可达"
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()
    (tmp / "dead.html").write_text(
        OLD_HTML.format(target=f"http://127.0.0.1:{dead_port}/nope"),
        encoding="utf-8",
    )

    yield {"base": base, "dir": tmp, "dead_port": str(dead_port)}

    server.shutdown()
    server.server_close()
    for name in ("old.html", "new.html", "dead.html"):
        try:
            (tmp / name).unlink()
        except OSError:
            pass
    try:
        tmp.rmdir()
    except OSError:
        pass


@pytest_asyncio.fixture
async def browser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            await browser.close()


@pytest_asyncio.fixture
async def page(browser):
    context = await browser.new_context(viewport={"width": 800, "height": 600})
    page = await context.new_page()
    try:
        yield page
    finally:
        await context.close()


def _mean_rgb(jpeg_b64: str) -> tuple[float, float, float]:
    image = PIL.open(io.BytesIO(base64.b64decode(jpeg_b64))).convert("RGB")
    pixels = (
        image.get_flattened_data()
        if hasattr(image, "get_flattened_data")
        else image.getdata()
    )
    pixels = list(pixels)
    count = len(pixels)
    return tuple(  # type: ignore[return-value]
        sum(channel) / count for channel in zip(*pixels)
    )


async def _click_link(page: Any, web_page: WebPage, selector: str) -> None:
    box = await page.locator(selector).bounding_box()
    assert box is not None
    await web_page.click(
        box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    )


# ---------------------------------------------------------------------------
# 验收 1: 改道成功后, 截图与定位来自新地址
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_click_target_blank_settles_before_screenshot(
    page: Any, site: dict[str, str]
) -> None:
    """点击 target=_blank 后, 紧接着的截图必须是新页面(不是旧页)."""
    web_page = WebPage(page)
    await web_page.navigate(f"{site['base']}/old.html")
    assert await page.evaluate("() => document.title") == "OLD PAGE"

    await _click_link(page, web_page, "#open")

    # click() 内部已经等完改道; 这里再做一次导航等待, 模拟真实动作链
    await web_page.wait_for_navigation()

    assert page.url.endswith("/new.html"), (
        f"main page did not reach the popup target, url={page.url!r}"
    )
    assert await page.evaluate("() => document.title") == "NEW PAGE"
    assert len(page.context.pages) == 1, "popup tab was not closed"

    # 截图内容来自新页面: 新页是浅绿(#ddffdd), 旧页是浅红(#ffdddd)
    red, green, _blue = _mean_rgb(await web_page.screenshot())
    assert green > red, (
        "screenshot still looks like the OLD (red) page: "
        f"mean red={red:.1f} green={green:.1f}"
    )


@pytest.mark.asyncio
async def test_click_target_blank_settles_before_locate(
    page: Any, site: dict[str, str]
) -> None:
    """点击 target=_blank 后, 紧接着的元素定位必须落在新文档上."""
    web_page = WebPage(page)
    await web_page.navigate(f"{site['base']}/old.html")
    text = await page.locator("#marker").text_content()
    assert text == "OLD PAGE"

    await _click_link(page, web_page, "#open")
    await web_page.wait_for_navigation()

    # 旧页面里根本没有 #price 之外的新结构; 用 XPath 取 marker 文本
    marker = await web_page.evaluate_javascript(
        "() => document.getElementById('marker').textContent"
    )
    assert marker == "NEW PAGE"

    info = await web_page.get_element_by_xpath("//h1[@id='marker']")
    assert info is not None, "element could not be located on the new document"
    assert info["rect"]["width"] > 0 and info["rect"]["height"] > 0


@pytest.mark.asyncio
async def test_data_url_pages_popup_is_settled(browser: Any) -> None:
    """起点/目标都用自造 data: 页时, 报告截图与采集内容一致.

    说明: Chromium 对 ``data:`` → ``data:`` 的 ``target=_blank`` 点击不会
    产生 popup(浏览器本身就不打开新上下文), 因此这里用"data: 起点 + 本地
    文件式 data: 目标"的组合验证另一条常见路径: 起点是 data:, 点击打开
    about:blank 再写入内容 → 这种情况没有可改道地址, 必须留下明确告警而
    不是静默把旧页面当成目标页。
    """
    context = await browser.new_context(viewport={"width": 800, "height": 600})
    try:
        page = await context.new_page()
        await page.goto("data:text/html,<h1 id='marker'>OLD DATA</h1>")
        web_page = WebPage(page)
        await page.evaluate(
            """() => {
                const a = document.createElement('a');
                a.id = 'open';
                a.target = '_blank';
                a.textContent = 'open';
                a.onclick = () => { window.open('about:blank'); };
                document.body.appendChild(a);
            }"""
        )
        await _click_link(page, web_page, "#open")
        # 没有可改道的地址: 主页面必须仍停留在原文档, 且不能假装成功
        assert await page.evaluate(
            "() => document.getElementById('marker').textContent"
        ) == "OLD DATA"
        assert len(context.pages) == 1
    finally:
        await context.close()


# ---------------------------------------------------------------------------
# 验收 2: 改道失败 → 后续采集能感知失败并留下日志
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreachable_popup_target_raises_and_logs(
    page: Any, site: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """目标不可达时, 后续采集必须失败并留下可诊断日志, 不能拿到旧页数据."""
    web_page = WebPage(page)
    await web_page.navigate(f"{site['base']}/dead.html")

    caplog.clear()
    with pytest.raises(PopupNavigationError) as exc_info:
        await _click_link(page, web_page, "#open")

    error = exc_info.value
    assert "popup target" in str(error).lower() or "/nope" in str(error)
    assert error.reason, "failure must carry a diagnostic reason"

    # 日志里必须留下失败痕迹(URL 或原因), 不能静默继续
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "FAILED" in messages or "Popup same-tab navigation FAILED" in messages
    assert "nope" in messages or "unreachable" in messages.lower()

    # 失败后, 读取页面状态必须继续报错, 而不是给出旧页内容
    with pytest.raises(PopupNavigationError):
        await web_page.screenshot()
    with pytest.raises(PopupNavigationError):
        await web_page.get_element_by_xpath("//h1[@id='marker']")

    # 显式导航到别处后失败状态被清除, 采集恢复
    await web_page.navigate(f"{site['base']}/new.html")
    assert await page.evaluate("() => document.title") == "NEW PAGE"
    assert len(await web_page.screenshot()) > 100


@pytest.mark.asyncio
async def test_successful_redirect_clears_previous_failure(
    page: Any, site: dict[str, str]
) -> None:
    """一次成功改道应清除此前记录的失败状态."""
    web_page = WebPage(page)
    await web_page.navigate(f"{site['base']}/dead.html")
    with pytest.raises(PopupNavigationError):
        await _click_link(page, web_page, "#open")

    await web_page.navigate(f"{site['base']}/old.html")
    await _click_link(page, web_page, "#open")
    await web_page.wait_for_navigation()

    assert page.url.endswith("/new.html")
    assert web_page._popup_redirect_error is None
    assert len(await web_page.screenshot()) > 100


# ---------------------------------------------------------------------------
# 行为约束 / 回归保护
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_same_tab_navigation_can_be_disabled(browser: Any) -> None:
    """显式关闭改道时不注册 popup 处理, 新 tab 保留(默认仍是开启)."""
    context = await browser.new_context()
    try:
        page = await context.new_page()
        web_page = WebPage(page, force_same_tab_navigation=False)
        await page.goto("data:text/html,<h1>start</h1>")
        await page.evaluate(
            """() => {
                const a = document.createElement('a');
                a.id = 'open'; a.target = '_blank';
                a.href = 'about:blank'; a.textContent = 'open';
                document.body.appendChild(a);
            }"""
        )

        popup_future = asyncio.ensure_future(
            page.wait_for_event("popup", timeout=10_000)
        )
        await _click_link(page, web_page, "#open")
        popup = await popup_future

        # 关闭改道时: 新 tab 没有被关掉, 主页面也没有被导航到它的地址
        assert not popup.is_closed(), "popup should have been left alone"
        assert len(context.pages) == 2
        assert page.url.startswith("data:text/html"), (
            f"main page should not have been redirected, url={page.url!r}"
        )
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_popup_handler_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    """popup 事件回调即使内部全面失败也不能把异常抛给事件循环."""

    class _ExplodingPopup:
        url = "http://example.invalid/x"

        def is_closed(self) -> bool:
            return False

        async def close(self) -> None:
            raise RuntimeError("boom")

        async def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

    class _DummyPage:
        def __init__(self) -> None:
            self.context = None

        def on(self, *args: Any, **kwargs: Any) -> None:
            return None

        def is_closed(self) -> bool:
            return False

        async def goto(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("goto boom")

    web_page = WebPage(_DummyPage(), force_same_tab_navigation=True)  # type: ignore[arg-type]

    # 不抛任何异常即通过; 失败应被记录到状态 + 日志里
    await web_page._on_popup_force_same_tab(  # noqa: SLF001
        _ExplodingPopup()  # type: ignore[arg-type]
    )
    assert web_page._popup_redirect_error is not None  # noqa: SLF001


def test_popup_navigation_error_is_runtime_error() -> None:
    error = PopupNavigationError("http://x/y", "boom")
    assert isinstance(error, RuntimeError)
    assert "http://x/y" in str(error)
    assert "boom" in str(error)


# ---------------------------------------------------------------------------
# navigate() 对"被另一次导航打断"的恢复逻辑(不依赖真实浏览器)
# ---------------------------------------------------------------------------


class _FakeNavigatingPage:
    """只实现 navigate() 用到的那部分 Page API."""

    def __init__(
        self,
        outcomes: list[str],
        final_url: str = "http://example.com/target",
    ) -> None:
        # outcomes: "interrupted" | "ok" | "other-error"
        self._outcomes = list(outcomes)
        self._final_url = final_url
        self.url = "about:blank"
        self.goto_calls = 0

    def on(self, *args: Any, **kwargs: Any) -> None:
        return None

    def is_closed(self) -> bool:
        return False

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else "ok"
        if outcome == "interrupted":
            raise RuntimeError(
                f'Navigation to "{url}" is interrupted by another navigation '
                'to "chrome-error://chromewebdata/"'
            )
        if outcome == "other-error":
            raise RuntimeError("Page.goto: net::ERR_NAME_NOT_RESOLVED")
        self.url = url

    async def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def wait_for_selector(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_navigate_retries_interrupted_navigation() -> None:
    """被打断一次后应重试并成功, 同时清掉此前的改道失败状态."""
    page = _FakeNavigatingPage(["interrupted", "ok"])
    web_page = WebPage(  # type: ignore[arg-type]
        page, force_same_tab_navigation=False
    )
    web_page._popup_redirect_error = PopupNavigationError("http://x/y", "boom")  # noqa: SLF001

    await web_page.navigate("http://example.com/target")

    assert page.goto_calls == 2
    assert web_page._popup_redirect_error is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_navigate_does_not_retry_after_reaching_target() -> None:
    """打断发生时若页面其实已到达目标, 不应再重试(否则会自打断)."""
    page = _FakeNavigatingPage(["interrupted"], final_url="")
    page.url = "http://example.com/target"
    web_page = WebPage(  # type: ignore[arg-type]
        page, force_same_tab_navigation=False
    )

    await web_page.navigate("http://example.com/target")

    assert page.goto_calls == 1


@pytest.mark.asyncio
async def test_navigate_does_not_retry_unrelated_errors() -> None:
    """非"被打断"类错误直接抛出, 不做无谓重试."""
    page = _FakeNavigatingPage(["other-error", "ok"])
    web_page = WebPage(  # type: ignore[arg-type]
        page, force_same_tab_navigation=False
    )

    with pytest.raises(RuntimeError, match="ERR_NAME_NOT_RESOLVED"):
        await web_page.navigate("http://example.com/target")

    assert page.goto_calls == 1
