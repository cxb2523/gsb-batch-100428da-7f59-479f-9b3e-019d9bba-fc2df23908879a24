"""NeedFree 抓取流程测试

使用本地构造的 JSON 响应（fake fetch）覆盖：
- 多页正常抓取并去重
- 单页重试后成功
- 重试耗尽后记录失败页且其余页仍写入
- 末页边界不产生多余请求
"""
import asyncio
import json
import re
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import NeedFree


# ---------- 本地构造的响应 ----------

def make_item_html(name, appid, is_free):
    """构造单个商品的搜索结果 HTML（结构与 Steam results_html 一致）"""
    discount = ""
    if is_free:
        # 与 NeedFree 解析逻辑匹配：discount div 向上 4 层是 <a> 商品行
        discount = ('<div><div><div>'
                    '<div class="search_discount_block" data-discount="100"></div>'
                    '</div></div></div>')
    return (f'<a href="https://store.steampowered.com/app/{appid}/{name}/" '
            f'class="search_result_row">{discount}'
            f'<span class="title">{name}</span></a>')


def make_page(total_count, items):
    """构造一页的 JSON 响应，items 为 (name, appid, is_free) 列表"""
    return {
        "total_count": total_count,
        "results_html": "".join(make_item_html(*it) for it in items)
    }


def make_fake_fetch(handler):
    """根据 handler(start, attempt) -> (json, error) 构造 fake fetch，并记录请求"""
    calls = []
    attempts = {}

    async def fake_fetch(session, url):
        start = int(re.search(r"start=(\d+)", url).group(1))
        calls.append(start)
        attempts[start] = attempts.get(start, 0) + 1
        return handler(start, attempts[start])

    return fake_fetch, calls


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    """跳过随机延迟，加速测试"""
    async def instant():
        return None
    monkeypatch.setattr(NeedFree, "random_delay", instant)


def run(coro):
    return asyncio.run(coro)


# ---------- 测试用例 ----------

def test_multi_page_crawl_and_dedup(monkeypatch):
    """多页正常抓取，跨页重复游戏名被去重"""
    total = 150
    page0_items = [(f"Game{i}", i, False) for i in range(98)]
    page0_items += [("Alpha", 1001, True), ("Beta", 1002, True)]
    page1_items = [(f"Game{i}", i, False) for i in range(98, 146)]
    page1_items += [("Beta", 1002, True), ("Gamma", 1003, True)]  # Beta 跨页重复

    pages = {0: make_page(total, page0_items), 100: make_page(total, page1_items)}

    def handler(start, attempt):
        return (pages[start], None)

    fake_fetch, calls = make_fake_fetch(handler)
    monkeypatch.setattr(NeedFree, "fetch_Steam_json_response", fake_fetch)

    free_list, failed_pages = run(NeedFree.crawl(session=None))

    assert failed_pages == []
    assert [item[0] for item in free_list] == ["Alpha", "Beta", "Gamma"]
    assert calls == [0, 100]  # 两页各请求一次


def test_retry_then_success(monkeypatch):
    """单页前两次失败、第三次成功，不记录失败页"""
    page = make_page(100, [("Alpha", 1001, True)] + [(f"G{i}", i, False) for i in range(99)])

    def handler(start, attempt):
        if attempt < 3:
            return (None, "HTTP 状态码 500")
        return (page, None)

    fake_fetch, calls = make_fake_fetch(handler)
    monkeypatch.setattr(NeedFree, "fetch_Steam_json_response", fake_fetch)

    free_list, failed_pages = run(NeedFree.crawl(session=None))

    assert failed_pages == []
    assert [item[0] for item in free_list] == ["Alpha"]
    assert calls == [0, 0, 0]  # 同一页尝试 3 次


def test_retry_exhausted_records_failure_and_continues(monkeypatch, tmp_path):
    """第 2 页重试耗尽后记录失败页，其余页仍正常抓取并写入"""
    total = 250
    page0 = make_page(total, [("Alpha", 1001, True)] + [(f"G{i}", i, False) for i in range(99)])
    page2 = make_page(total, [("Gamma", 1003, True)] + [(f"G{i}", i, False) for i in range(100, 149)])

    def handler(start, attempt):
        if start == 100:
            return (None, "HTTP 状态码 500")  # 永远失败
        return ({0: page0, 200: page2}[start], None)

    fake_fetch, calls = make_fake_fetch(handler)
    monkeypatch.setattr(NeedFree, "fetch_Steam_json_response", fake_fetch)

    free_list, failed_pages = run(NeedFree.crawl(session=None))

    # 失败页被记录：页码、起始位置、原因
    assert len(failed_pages) == 1
    assert failed_pages[0]["page"] == 2
    assert failed_pages[0]["start"] == 100
    assert "500" in failed_pages[0]["reason"]

    # 其余页的免费游戏仍被收集
    assert [item[0] for item in free_list] == ["Alpha", "Gamma"]

    # 失败页恰好尝试 MAX_RETRY_TIMES 次，不多不少
    assert calls.count(100) == NeedFree.MAX_RETRY_TIMES

    # 写入文件包含 failed_pages 字段
    out = tmp_path / "free_goods_detail.json"
    NeedFree.save_results(free_list, failed_pages, path=str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total_count"] == 2
    assert len(data["free_list"]) == 2
    assert data["failed_pages"] == failed_pages
    assert "update_time" in data


def test_last_page_boundary_no_extra_request(monkeypatch):
    """商品总数恰好为整页倍数时，末页之后不产生多余请求"""
    total = 200
    page0 = make_page(total, [(f"G{i}", i, False) for i in range(100)])
    page1 = make_page(total, [(f"G{i}", i, False) for i in range(100, 200)])

    def handler(start, attempt):
        assert start < total, f"不应请求 start={start} 的空页"
        return ({0: page0, 100: page1}[start], None)

    fake_fetch, calls = make_fake_fetch(handler)
    monkeypatch.setattr(NeedFree, "fetch_Steam_json_response", fake_fetch)

    free_list, failed_pages = run(NeedFree.crawl(session=None))

    assert failed_pages == []
    assert calls == [0, 100]  # 恰好两个请求，没有第三个空页请求
