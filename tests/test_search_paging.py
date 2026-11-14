"""搜索翻页：/search 的 offset + logid(search_id) 链路与停止语义。

这些测试不碰真实抖音：dy-cli 的两个子进程入口（`run_dy` / `_run_dy_query`）
被替换成假的，只验证「参数怎么传下去」和「上游各形态怎么翻译成翻页结局」。

关键回归点：第二页只挪 offset、不带上一页 logid 时，抖音会回空列表；
本地服务必须把 logid 原样传给 dy_query，客户端才可能连续翻页。
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import douyin_http as H  # noqa: E402
import dy_paging as P  # noqa: E402

client = TestClient(H.app)


def _row(aid: str) -> dict:
    return {"aweme_info": {"aweme_id": aid, "desc": f"视频 {aid}",
                           "author": {"nickname": "作者"}}}


def _raw_page(rows, *, logid="", has_more=None, nil_type=""):
    data = {"data": rows, "extra": {"logid": logid}}
    if has_more is not None:
        data["has_more"] = has_more
    if nil_type:
        data["search_nil_info"] = {"search_nil_type": nil_type}
    return data


class _Calls:
    """记录 /search 实际走到哪个子进程入口、参数长什么样。"""

    def __init__(self):
        self.dy = []
        self.query = []
        self.dy_result = None
        self.query_result = None

    def install(self, monkeypatch):
        def fake_dy(args, timeout=150):
            self.dy.append(list(args))
            return self.dy_result if self.dy_result is not None else {
                "ok": True, "data": _raw_page([], logid="")}

        def fake_query(args, timeout=180):
            self.query.append(list(args))
            return self.query_result if self.query_result is not None else {
                "ok": True, "data": _raw_page([], logid="")}

        monkeypatch.setattr(H, "run_dy", fake_dy)
        monkeypatch.setattr(H, "_run_dy_query", fake_query)
        return self


def _post(body):
    res = client.post("/search", json=body)
    assert res.status_code == 200
    return res.json()


# ──────────────────────────────────────────────────────────────────────
# 纯函数：把一页原始响应翻译成翻页元信息
# ──────────────────────────────────────────────────────────────────────
def test_first_page_paging_metadata():
    page = P.paging_from_result(
        _raw_page([_row("1"), _row("2")], logid="LOG-A", has_more=1),
        offset=0, count=10)
    assert page["items"] == 2
    assert page["next_offset"] == 2
    assert page["logid"] == "LOG-A"
    assert page["has_more"] is True
    assert page["outcome"] == "completed"


def test_explicit_no_more():
    page = P.paging_from_result(
        _raw_page([_row("1")], logid="L", has_more=0), offset=10, count=10)
    assert page["outcome"] == "no_more"
    assert page["has_more"] is False
    assert page["next_offset"] == 11


def test_empty_page_is_not_no_more():
    # 空列表但上游没说「没有更多」——绝不能冒充「已采完」。
    page = P.paging_from_result(
        _raw_page([], logid="L", has_more=1), offset=10, count=10)
    assert page["outcome"] == "empty_page"
    assert page["items"] == 0
    assert page["next_offset"] == 10


def test_verify_check_aborts_even_with_rows():
    page = P.paging_from_result(
        _raw_page([_row("1")], logid="L", has_more=1, nil_type="verify_check"),
        offset=10, count=10)
    assert page["outcome"] == "challenge_required"
    assert "challenge_required" in P.ABORT_OUTCOMES


def test_error_classification_keeps_explicit_codes():
    assert P.classify_error("HTTP 429 太频繁") == "rate_limited"
    assert P.classify_error("请先登录") == "not_authenticated"
    assert P.classify_error("JSON 解析失败") == "parse_failed"
    assert P.classify_error("验证码未通过") == "challenge_required"
    assert P.classify_error("任意错误", "challenge_required") == "challenge_required"
    assert P.classify_error("任意错误") == "backend_unavailable"


# ──────────────────────────────────────────────────────────────────────
# /search：首屏行为不变
# ──────────────────────────────────────────────────────────────────────
def test_first_page_without_logid_uses_dy_search(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.dy_result = {"ok": True, "data": _raw_page(
        [_row("111"), _row("222")], logid="LOG-1", has_more=1)}
    body = _post({"keyword": "猫", "count": 5})

    assert body["ok"] is True
    assert body["outcome"] == "completed"
    assert body["next_offset"] == 2
    assert body["logid"] == "LOG-1"
    assert body["has_more"] is True
    assert body["chained_with_logid"] is False
    assert len(body["data"]["data"]) == 2
    assert len(calls.dy) == 1 and not calls.query
    # 首屏命令和改动前完全一致：没有 --offset / --logid。
    assert calls.dy[0][:2] == ["search", "猫"]
    assert "--offset" not in calls.dy[0]
    assert "--logid" not in calls.dy[0]


# ──────────────────────────────────────────────────────────────────────
# /search：第二页必须把上一页 logid 带上
# ──────────────────────────────────────────────────────────────────────
def test_second_page_passes_previous_logid(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.query_result = {"ok": True, "data": {
        "result": _raw_page([_row("333")], logid="LOG-2", has_more=1),
        "paging": P.paging_from_result(
            _raw_page([_row("333")], logid="LOG-2", has_more=1),
            offset=2, count=10),
    }}
    body = _post({"keyword": "猫", "count": 10, "offset": 2, "logid": "LOG-1"})

    assert body["ok"] is True
    assert body["chained_with_logid"] is True
    assert body["logid"] == "LOG-2"
    assert body["next_offset"] == 3
    assert len(calls.query) == 1 and not calls.dy
    args = calls.query[0]
    assert args[:2] == ["search", "猫"]
    assert args[args.index("--offset") + 1] == "2"
    assert args[args.index("--logid") + 1] == "LOG-1"
    # 返回的条目来自 dy_query 的 data.result
    assert [r["aweme_info"]["aweme_id"] for r in body["data"]["data"]] == ["333"]


def test_offset_without_logid_still_goes_through_query_path(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.query_result = {"ok": True, "data": {
        "result": _raw_page([], has_more=1), "paging": {
            "offset": 10, "count": 0, "requested_count": 10, "items": 0,
            "next_offset": 10, "logid": "", "has_more": True,
            "outcome": "empty_page"}}}
    body = _post({"keyword": "猫", "count": 10, "offset": 10})
    assert len(calls.query) == 1
    assert "--logid" not in calls.query[0]
    assert body["outcome"] == "empty_page"
    assert body["ok"] is True
    assert body["has_more"] is True


# ──────────────────────────────────────────────────────────────────────
# /search：中止错误
# ──────────────────────────────────────────────────────────────────────
def test_verify_check_returns_challenge_required_and_keeps_rows(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.query_result = {
        "ok": False,
        "error": {"code": "challenge_required",
                  "message": "抖音搜索返回 verify_check：需要人机验证"},
        "paging": {**P.error_paging(offset=10, count=10, outcome="challenge_required"),
                   "items": 1},
        "data": {"result": _raw_page([_row("999")], nil_type="verify_check")},
    }
    body = _post({"keyword": "猫", "count": 10, "offset": 10, "logid": "LOG-X"})

    assert body["ok"] is False
    assert body["outcome"] == "challenge_required"
    assert body["error"]["code"] == "challenge_required"
    # 已拿到的条目不能因为中止就被丢掉
    assert [r["aweme_info"]["aweme_id"] for r in body["data"]["data"]] == ["999"]
    assert body["chained_with_logid"] is True


def test_upstream_verify_check_in_success_envelope_aborts(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.dy_result = {"ok": True, "data": _raw_page(
        [_row("1")], logid="L", has_more=1, nil_type="verify_check")}
    body = _post({"keyword": "猫", "count": 10})
    assert body["ok"] is False
    assert body["outcome"] == "challenge_required"
    assert len(body["data"]["data"]) == 1


def test_rate_limit_error_maps_to_outcome(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.query_result = {"ok": False,
                          "error": {"code": "rate_limited", "message": "429 频繁"}}
    body = _post({"keyword": "猫", "count": 10, "offset": 10, "logid": "L"})
    assert body["ok"] is False
    assert body["outcome"] == "rate_limited"
    assert body["items"] == 0
    assert body["next_offset"] == 10


def test_plain_string_error_is_classified(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.dy_result = {"ok": False, "error": "请先登录后再搜索"}
    body = _post({"keyword": "猫", "count": 10})
    assert body["ok"] is False
    assert body["outcome"] == "not_authenticated"


def test_parse_failure_stops_with_parse_failed(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.query_result = {"ok": False,
                          "error": {"code": "parse_failed", "message": "JSON 解析失败"}}
    body = _post({"keyword": "猫", "count": 10, "offset": 2, "logid": "L"})
    assert body["outcome"] == "parse_failed"


def test_no_more_reported(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.query_result = {"ok": True, "data": {
        "result": _raw_page([_row("5")], logid="L2", has_more=0),
        "paging": P.paging_from_result(
            _raw_page([_row("5")], logid="L2", has_more=0), offset=10, count=10)}}
    body = _post({"keyword": "猫", "count": 10, "offset": 10, "logid": "L"})
    assert body["ok"] is True
    assert body["outcome"] == "no_more"
    assert body["has_more"] is False


def test_invalid_request_never_calls_upstream(monkeypatch):
    calls = _Calls().install(monkeypatch)
    body = _post({"keyword": "   ", "count": 10})
    assert body["ok"] is False
    assert body["outcome"] == "invalid_args"
    assert not calls.dy and not calls.query


def test_bad_offset_is_200_with_invalid_args(monkeypatch):
    # 本服务的约定是失败也回 HTTP 200 + ok:false，不能因为 offset 写坏就 422。
    calls = _Calls().install(monkeypatch)
    for bad in (-3, "abc", 2.5):
        body = _post({"keyword": "猫", "count": 10, "offset": bad})
        assert body["ok"] is False
        assert body["outcome"] == "invalid_args"
        assert body["offset"] == 0
    assert not calls.dy and not calls.query


def test_numeric_string_offset_is_accepted(monkeypatch):
    calls = _Calls().install(monkeypatch)
    calls.query_result = {"ok": True, "data": {
        "result": _raw_page([_row("8")], logid="L3", has_more=0)}}
    body = _post({"keyword": "猫", "count": 10, "offset": "20", "logid": "L"})
    assert body["ok"] is True
    assert body["offset"] == 20
    assert calls.query[0][calls.query[0].index("--offset") + 1] == "20"
