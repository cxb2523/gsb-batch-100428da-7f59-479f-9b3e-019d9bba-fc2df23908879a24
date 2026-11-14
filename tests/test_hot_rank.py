"""bili hot rank：多页采集、已看过滤、结束原因与失败保留。

所有数据都是本地构造的：`collect_rank(..., fetch=...)` 注入假的取页函数，
CLI 路径则 monkeypatch `hot._rank_page`，测试不访问网络。
"""

import asyncio
import json

import pytest
from typer.testing import CliRunner

from bili_cli import cards
from bili_cli.commands import hot
from bili_cli.errors import BiliError
from bili_cli.main import app

PAGE = hot.RANK_PAGE_SIZE
RID_TECH = hot.RANK_ZONES["tech"].value["rid"]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))
    return tmp_path


def _item(n: int) -> dict:
    """网页同形的排行榜条目（video_card 的输入）。"""
    return {
        "bvid": f"BV1xx41{n:05d}",
        "title": f"视频 {n}",
        "owner": {"mid": n, "name": f"UP{n}"},
        "duration": 60,
        "pubdate": 1700000000 + n,
        "stat": {"view": n * 10, "like": n, "danmaku": n, "reply": n},
    }


def _page(ns, raw=None, *, paged=True, no_more=False):
    items = [_item(n) for n in ns]
    return {"items": items, "raw": len(items) if raw is None else raw, "payload": {"list": items}, "paged": paged, "no_more": no_more}


def _fetch(pages: dict):
    """按页码返回构造好的页；记录请求顺序。"""
    calls = []

    async def fetch(zone, page):
        calls.append((zone, page))
        result = pages[page]
        if isinstance(result, BaseException):
            raise result
        return result

    return fetch, calls


def _run(pages, *, zone="tech", pages_limit=1, limit=30, unseen=False):
    fetch, calls = _fetch(pages)
    result, raw_pages = asyncio.run(hot.collect_rank(zone, pages_limit, limit, unseen, fetch=fetch))
    return result, raw_pages, calls


def test_normal_paging_requests_pages_in_order():
    pages = {1: _page(range(1, PAGE + 1)), 2: _page(range(PAGE + 1, 2 * PAGE + 1))}
    result, _, calls = _run(pages, pages_limit=2, limit=45)

    assert calls == [("tech", 1), ("tech", 2)]          # 按序、不并发
    assert result["pages_fetched"] == 2
    assert result["page_stats"] == [
        {"page": 1, "raw": PAGE, "skipped": 0, "kept": PAGE},
        {"page": 2, "raw": PAGE, "skipped": 0, "kept": PAGE},
    ]
    assert len(result["items"]) == 45 and result["stop_reason"] == hot.STOP_LIMIT
    # 名次跨页连续
    assert result["items"][0]["rank"] == 1 and result["items"][-1]["rank"] == 45


def test_unseen_filter_pushes_on_to_next_page():
    seen = [_item(n)["bvid"] for n in range(1, PAGE + 1)]   # 第一页全部看过
    for bvid in seen:
        cards.mark_seen(bvid)
    pages = {1: _page(range(1, PAGE + 1)), 2: _page(range(PAGE + 1, 2 * PAGE + 1))}

    result, _, calls = _run(pages, pages_limit=2, limit=10, unseen=True)

    assert calls == [("tech", 1), ("tech", 2)]          # 第一页被过滤空，于是继续翻
    assert result["page_stats"][0] == {"page": 1, "raw": PAGE, "skipped": PAGE, "kept": 0}
    assert result["page_stats"][1]["skipped"] == 0
    assert len(result["items"]) == 10 and result["stop_reason"] == hot.STOP_LIMIT
    # 过滤后名次保留空档：第二页第一条仍是第 PAGE+1 名
    assert result["items"][0]["rank"] == PAGE + 1


def test_pages_limit_stops_with_exactly_that_many_requests():
    pages = {i: _page(range((i - 1) * PAGE + 1, i * PAGE + 1)) for i in range(1, 6)}

    result, _, calls = _run(pages, pages_limit=3, limit=100)

    assert len(calls) == 3 and result["pages_fetched"] == 3
    assert result["pages_limit"] == 3
    assert result["stop_reason"] == hot.STOP_PAGES
    assert len(result["items"]) == 3 * PAGE


def test_no_more_data_stops_before_pages_limit():
    # 第二页接口说没有更多了，不应再请求第三页
    pages = {1: _page(range(1, PAGE + 1)), 2: _page(range(PAGE + 1, PAGE + 8), no_more=True)}

    result, _, calls = _run(pages, pages_limit=5, limit=200)

    assert calls == [("tech", 1), ("tech", 2)]
    assert result["pages_fetched"] == 2
    assert result["stop_reason"] == hot.STOP_NO_MORE
    assert len(result["items"]) == PAGE + 7


def test_failure_on_a_page_keeps_earlier_results_and_names_the_page():
    pages = {1: _page(range(1, PAGE + 1)), 2: BiliError("network_error", "连接被重置"), 3: _page(range(1, PAGE + 1))}

    result, _, calls = _run(pages, pages_limit=3, limit=100)

    assert calls == [("tech", 1), ("tech", 2)]          # 失败后不再继续
    assert len(result["items"]) == PAGE                 # 第一页的结果还在
    assert result["pages_fetched"] == 2
    assert result["stop_reason"] == hot.STOP_ERROR
    assert result["error"] == {"page": 2, "code": "network_error", "message": "连接被重置"}
    assert [s["page"] for s in result["page_stats"]] == [1]   # 失败页不当作空页记一条


def test_real_bilibili_error_keeps_its_code():
    from bilibili_api.exceptions import ResponseCodeException

    pages = {1: _page(range(1, PAGE + 1)), 2: ResponseCodeException(-352, "风控校验失败", {})}

    result, _, _ = _run(pages, pages_limit=2, limit=100)

    assert result["stop_reason"] == hot.STOP_ERROR
    assert result["error"]["page"] == 2
    assert result["error"]["code"] == "risk_control"     # from_exception 的映射
    assert result["error"]["bili_code"] == -352


def test_failure_on_first_page_raises_with_page_in_detail():
    fetch, _ = _fetch({1: BiliError("api_error", "接口挂了", detail={"bili_code": 1})})

    with pytest.raises(BiliError) as exc:
        asyncio.run(hot.collect_rank("tech", 2, 30, False, fetch=fetch))

    assert exc.value.code == "api_error" and exc.value.detail["page"] == 1
    assert exc.value.detail["bili_code"] == 1


def test_filtered_count_is_reported_per_page():
    for bvid in [_item(n)["bvid"] for n in (2, 4, 6)]:
        cards.mark_seen(bvid)
    pages = {1: _page(range(1, PAGE + 1))}

    result, _, _ = _run(pages, limit=100, unseen=True)

    assert result["page_stats"] == [{"page": 1, "raw": PAGE, "skipped": 3, "kept": PAGE - 3}]
    assert len(result["items"]) == PAGE - 3
    assert [i["rank"] for i in result["items"]][:3] == [1, 3, 5]


def test_defaults_are_one_page_and_cli_passes_pages(monkeypatch):
    calls = []

    async def fake_page(zone, page):
        calls.append((zone, page))
        return _page(range((page - 1) * PAGE + 1, page * PAGE + 1))

    monkeypatch.setattr(hot, "_rank_page", fake_page)
    runner = CliRunner()

    out = json.loads(runner.invoke(app, ["hot", "rank", "--zone", "tech"]).stdout)["data"]
    assert calls == [("tech", 1)] and out["pages_fetched"] == 1
    assert out["stop_reason"] == hot.STOP_LIMIT and len(out["items"]) == 30   # 默认 --limit 30

    calls.clear()
    out = json.loads(runner.invoke(app, ["hot", "rank", "--zone", "tech", "--pages", "3", "--limit", "60", "--unseen"]).stdout)["data"]
    assert calls == [("tech", 1), ("tech", 2)]
    assert out["pages_fetched"] == 2 and out["stop_reason"] == hot.STOP_LIMIT


def test_cli_raw_returns_raw_pages_only(monkeypatch):
    async def fake_page(zone, page):
        return _page(range((page - 1) * PAGE + 1, page * PAGE + 1))

    monkeypatch.setattr(hot, "_rank_page", fake_page)

    # 单页时就是 B 站那一页的原始返回（和加 --pages 之前一致）
    one = json.loads(CliRunner().invoke(app, ["--raw", "hot", "rank", "--zone", "tech"]).stdout)["data"]
    assert one["list"] == [_item(n) for n in range(1, PAGE + 1)]

    # 多页时是每页原始返回组成的列表
    many = json.loads(CliRunner().invoke(app, ["--raw", "hot", "rank", "--zone", "tech", "--pages", "2", "--limit", "60"]).stdout)["data"]
    assert isinstance(many, list) and len(many) == 2
    assert many[0]["list"] == [_item(n) for n in range(1, PAGE + 1)]
    assert many[1]["list"][0] == _item(PAGE + 1)


def test_cli_partial_failure_still_exits_zero(monkeypatch):
    async def fake_page(zone, page):
        if page == 2:
            raise BiliError("network_error", "连接被重置")
        return _page(range(1, PAGE + 1))

    monkeypatch.setattr(hot, "_rank_page", fake_page)
    result = CliRunner().invoke(app, ["hot", "rank", "--zone", "tech", "--pages", "2", "--limit", "60"])
    out = json.loads(result.stdout)

    assert result.exit_code == 0 and out["ok"] is True
    assert out["data"]["stop_reason"] == hot.STOP_ERROR
    assert out["data"]["error"] == {"page": 2, "code": "network_error", "message": "连接被重置"}
    assert len(out["data"]["items"]) == PAGE


def test_app_rank_item_is_converted_to_web_shape():
    app_item = {
        "title": "t", "param": "1000", "goto": "av", "play": 7, "danmaku": 3, "mid": 9,
        "name": "up", "reply": 2, "like": 5, "pub_date": 1700000000, "duration": 61,
    }
    card = cards.video_card(hot._app_video(app_item))

    assert card["bvid"] == "BV1xx411c7fV"
    assert card["author"] == {"mid": 9, "name": "up"}
    assert card["stat"] == {"view": 7, "like": 5, "danmaku": 3, "reply": 2}
    assert card["pubdate"].startswith("2023-11-1") and hot._app_video({"param": "not-an-aid"}) is None


def test_rank_page_pages_the_app_endpoint_and_detects_the_end(monkeypatch):
    calls = []

    class FakeApi:
        def __init__(self, url, method="GET", **kw):
            assert url == hot.RANK_URL and method == "GET"

        def update_params(self, **params):
            calls.append(params)
            return self

        @property
        def result(self):
            async def res():
                return [{"param": "1000", "title": "t", "play": 1, "mid": 1, "name": "up", "reply": 0, "like": 0, "duration": 60}]

            return res()

    monkeypatch.setattr(hot, "Api", FakeApi)

    page = asyncio.run(hot._rank_page("tech", 2))
    assert calls == [{"rid": RID_TECH, "pn": 2, "ps": PAGE}]
    assert page["raw"] == 1 and page["paged"] is True
    assert page["no_more"] is True          # 只有 1 条 < 每页 30 条：翻到头了
    assert page["items"][0]["bvid"] == "BV1xx411c7fV"


def test_rank_page_uses_the_whole_list_for_unpaged_zones(monkeypatch):
    async def fake_get_rank(zone):
        assert zone == hot.RANK_ZONES["rookie"]
        return {"list": [_item(1), _item(2)]}

    monkeypatch.setattr(hot.rank, "get_rank", fake_get_rank)

    page = asyncio.run(hot._rank_page("rookie", 1))
    assert page["raw"] == 2 and page["paged"] is False and page["no_more"] is True
    assert page["payload"] == {"list": [_item(1), _item(2)]}
