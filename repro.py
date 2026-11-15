#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日头条主页推送新闻流（Feed）纯协议复现与实时抓取脚本

原理：
  1. 调用本地 Node.js 沙箱运行 bdms.js（JSVMP），结合轻量 DOM/XHR 垫片模拟真实浏览器请求；
  2. 提取 localStorage.xmst 种子（msToken），在 XHR.send() 阶段生成 168 位 a_bogus 签名；
  3. 采用「现签现发」机制在毫秒级内发起 HTTP GET 请求，直接获取包含完整新闻流数据的 JSON 响应。

翻页采集：
  - 按频道连续翻页，每页「现签现发」（不预签、不缓存签名）；
  - 每页追加一条结局记录（频道、max_behot_time、阶段、成败、失败原因、本页/累计条数）；
  - 任何一页失败都保留此前已解析的新闻，并明确记录卡在 sign / request / parse 哪一步；
  - 翻页游标完全由响应的 next.max_behot_time 推进，绝不重复请求同一游标。

运行环境：
  - Python 3.8+
  - Node.js >= 18
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Windows 下统一标准输出/错误为 UTF-8，防止控制台中文乱码
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from sign_abogus import build_feed_url, sign_abogus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("toutiao_feed_repro")

HERE = Path(__file__).resolve().parent
SAMPLE_OUTPUT = HERE / "sample_data.json"

# 统一 User-Agent，与签名指纹对齐
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "referer": "https://www.toutiao.com/",
    "user-agent": USER_AGENT,
}

# 翻页过程中的三个阶段
STAGE_SIGN = "sign"
STAGE_REQUEST = "request"
STAGE_PARSE = "parse"


class FeedParseError(Exception):
    """响应体结构不符合 feed 接口约定时抛出"""


def _default_http_get(url: str, headers: Dict[str, str], timeout: int) -> Tuple[int, bytes]:
    """默认 HTTP 实现：返回 (状态码, 响应体字节)。非 200 不抛异常，由调用方判定。"""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        # 非 2xx：urllib 会抛 HTTPError，这里取出真实状态码交给上层记录
        try:
            body = exc.read()
        except Exception:
            body = b""
        return exc.code, body


def _coerce_cursor(value: Any) -> Optional[int]:
    """把响应里的 max_behot_time 游标转成 int，转不了视为不可用"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _map_news_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """单条新闻字段映射（title/source/publish_time/url/read_count/comments_count/group_id）"""
    title = item.get("title") or item.get("Abstract")
    source = item.get("source") or item.get("media_name") or "今日头条"
    behot_time = item.get("behot_time")
    pub_time = (
        datetime.fromtimestamp(behot_time).strftime("%Y-%m-%d %H:%M:%S")
        if behot_time
        else "刚刚"
    )
    group_id = item.get("group_id") or item.get("item_id")
    article_url = (
        f"https://www.toutiao.com/article/{group_id}/"
        if group_id
        else item.get("article_url", "")
    )
    return {
        "title": str(title).strip(),
        "source": source,
        "publish_time": pub_time,
        "url": article_url,
        "read_count": item.get("read_count", 0),
        "comments_count": item.get("comments_count", 0),
        "group_id": str(group_id) if group_id else "",
    }


def parse_feed_payload(payload: Any) -> Tuple[List[Dict[str, Any]], int, Optional[int]]:
    """
    解析一页 feed 响应体（已解码的 JSON）。

    :return: (本页新闻列表, 本页跳过条数, 下一页 max_behot_time 游标或 None)
    :raises FeedParseError: message 非 success、data 缺失或不是列表
    """
    if not isinstance(payload, dict):
        raise FeedParseError("响应体不是 JSON 对象")

    if payload.get("message") != "success":
        raise FeedParseError(f"接口 message 非 success: {payload.get('message')!r}")

    data = payload.get("data")
    if not isinstance(data, list):
        raise FeedParseError("data 缺失或不是列表，视为解析失败")

    news_list: List[Dict[str, Any]] = []
    skipped = 0
    for item in data:
        if not isinstance(item, dict):
            skipped += 1
            continue
        if not (item.get("title") or item.get("Abstract")):
            skipped += 1
            continue
        news_list.append(_map_news_item(item))

    next_cursor: Optional[int] = None
    nxt = payload.get("next")
    if isinstance(nxt, dict):
        next_cursor = _coerce_cursor(nxt.get("max_behot_time"))

    return news_list, skipped, next_cursor


def fetch_channel_feed_pages(
    channel_id: int = 0,
    max_behot_time: Optional[int] = None,
    max_pages: int = 3,
    min_count: Optional[int] = None,
    category: str = "pc_profile_recommend",
    timeout: int = 15,
    sign_fn: Callable[..., Dict[str, Any]] = sign_abogus,
    http_get: Optional[Callable[[str, Dict[str, str], int], Tuple[int, bytes]]] = None,
) -> Dict[str, Any]:
    """
    按频道连续翻页抓取新闻流，每页现签现发。

    :param channel_id: 频道 ID，默认 0（推荐）
    :param max_behot_time: 起始翻页游标；None 表示从当前时间开始
    :param max_pages: 最多抓取页数
    :param min_count: 期望累计条数，达到即停止；None 表示不限
    :param category: 分类标识
    :param timeout: 单页请求超时（秒）
    :param sign_fn: 签名函数（默认 sign_abogus，现签现发）
    :param http_get: HTTP 实现，便于注入本地样例做离线验收；默认走 urllib
    :return: {
        "channel_id": int,
        "news": [...],          # 已累计解析的新闻（失败时也保留）
        "pages": [...],         # 每页结局记录
        "stop_reason": str,     # 停止翻页的原因
        "failed": bool,         # 是否因失败而停止
    }
    """
    if http_get is None:
        http_get = _default_http_get
    if max_pages < 1:
        max_pages = 1

    # 起始游标：未指定时取当前时间，保证记录里的 max_behot_time 是真实请求值
    cursor = max_behot_time if max_behot_time is not None else int(time.time())

    news_all: List[Dict[str, Any]] = []
    pages: List[Dict[str, Any]] = []
    stop_reason = "达到最多页数"
    failed = False

    def _record(stage: str, success: bool, reason: str, page_count: int, skipped: int) -> None:
        pages.append({
            "channel_id": channel_id,
            "max_behot_time": cursor,
            "stage": stage,
            "success": success,
            "reason": reason,
            "page_count": page_count,
            "skipped_count": skipped,
            "total_count": len(news_all),
        })

    for page_no in range(1, max_pages + 1):
        base_url = build_feed_url(
            channel_id=channel_id, category=category, max_behot_time=cursor
        )
        logger.info(
            "第 %d/%d 页: channel_id=%s max_behot_time=%s", page_no, max_pages, channel_id, cursor
        )

        # ---- 阶段 1：现签现发，签名失败则不得发起 HTTP 请求 ----
        try:
            signed = sign_fn(base_url, refresh_snapshot=False)
            signed_url = signed.get("signed_url")
            if not signed_url or not signed.get("a_bogus"):
                raise RuntimeError("签名结果缺少 signed_url 或 a_bogus")
        except Exception as exc:
            logger.error("第 %d 页签名失败: %s", page_no, exc)
            _record(STAGE_SIGN, False, f"签名失败: {exc}", 0, 0)
            stop_reason = "签名失败，未发起该页请求，停止翻页"
            failed = True
            break

        # ---- 阶段 2：HTTP 请求 ----
        try:
            status, body = http_get(signed_url, DEFAULT_HEADERS, timeout)
        except Exception as exc:
            logger.error("第 %d 页请求异常: %s", page_no, exc)
            _record(STAGE_REQUEST, False, f"请求异常: {exc}", 0, 0)
            stop_reason = "请求阶段失败，保留已解析结果并停止翻页"
            failed = True
            break

        if status != 200:
            logger.error("第 %d 页响应状态码异常: %s", page_no, status)
            _record(STAGE_REQUEST, False, f"HTTP 状态码非 200: {status}", 0, 0)
            stop_reason = "HTTP 非 200，保留已解析结果并停止翻页"
            failed = True
            break

        # ---- 阶段 3：解析响应 ----
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError) as exc:
            logger.error("第 %d 页响应体不是合法 JSON: %s", page_no, exc)
            _record(STAGE_PARSE, False, f"响应体不是合法 JSON: {exc}", 0, 0)
            stop_reason = "响应体非法 JSON，保留已解析结果并停止翻页"
            failed = True
            break

        try:
            page_news, skipped, next_cursor = parse_feed_payload(payload)
        except FeedParseError as exc:
            logger.error("第 %d 页解析失败: %s", page_no, exc)
            _record(STAGE_PARSE, False, str(exc), 0, 0)
            stop_reason = "响应解析失败，保留已解析结果并停止翻页"
            failed = True
            break

        news_all.extend(page_news)
        _record(STAGE_PARSE, True, "", len(page_news), skipped)
        logger.info(
            "第 %d 页解析成功: 本页 %d 条，跳过 %d 条，累计 %d 条",
            page_no, len(page_news), skipped, len(news_all),
        )

        # ---- 停止条件判定 ----
        if not page_news:
            stop_reason = "本页解析条数为 0，停止翻页"
            break
        if min_count is not None and len(news_all) >= min_count:
            stop_reason = f"达到期望条数 ({len(news_all)}/{min_count})，停止翻页"
            break
        if next_cursor is None:
            stop_reason = "响应未提供可用翻页游标，停止翻页"
            break
        if next_cursor == cursor:
            stop_reason = "下一页游标与本页相同，为避免重复请求停止翻页"
            break
        cursor = next_cursor

    return {
        "channel_id": channel_id,
        "news": news_all,
        "pages": pages,
        "stop_reason": stop_reason,
        "failed": failed,
    }


def fetch_toutiao_homepage_news(
    channel_id: int = 0,
    category: str = "pc_profile_recommend",
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    """
    抓取今日头条主页最新推送新闻（单页，兼容旧调用方式）

    :param channel_id: 频道 ID，默认 0（推荐）
    :param category: 分类标识，默认 pc_profile_recommend
    :param timeout: 网络请求超时时间（秒）
    :return: 解析后的结构化新闻列表
    """
    result = fetch_channel_feed_pages(
        channel_id=channel_id, max_pages=1, category=category, timeout=timeout
    )
    return result["news"]


# ---------------------------------------------------------------------------
# 离线自测：自造本地样例响应，验证游标推进与失败保留（不联网、不调 Node）
# ---------------------------------------------------------------------------

def _make_payload(items: List[Any], next_cursor: Optional[int] = None) -> bytes:
    payload: Dict[str, Any] = {"message": "success", "data": items}
    if next_cursor is not None:
        payload["next"] = {"max_behot_time": next_cursor}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _make_item(idx: int, behot: int) -> Dict[str, Any]:
    return {
        "title": f"样例新闻 {idx}",
        "source": "自测源",
        "behot_time": behot,
        "group_id": 7300000000 + idx,
        "read_count": idx * 10,
        "comments_count": idx,
    }


def run_self_test() -> int:
    """本地样例验收：游标推进、翻页去重、各阶段失败保留。返回 0 表示全部通过。"""
    failures: List[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        status = "PASS" if cond else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    ok_sign = lambda url, **kw: {"signed_url": url + "&a_bogus=" + "x" * 168, "a_bogus": "x" * 168}

    # 场景 1：正常翻 3 页，游标 1000 -> 900 -> 800 -> 无游标停止
    print("场景 1: 正常翻页，游标推进直至响应不再提供游标")
    pages_payload = {
        1000: (200, _make_payload([_make_item(1, 1000), _make_item(2, 990)], next_cursor=900)),
        900: (200, _make_payload([_make_item(3, 890)], next_cursor=800)),
        800: (200, _make_payload([_make_item(4, 790)], next_cursor=None)),
    }
    requested_cursors: List[int] = []

    def http_ok(url: str, headers: Dict[str, str], timeout: int) -> Tuple[int, bytes]:
        cursor = int(url.split("max_behot_time=")[1].split("&")[0])
        requested_cursors.append(cursor)
        return pages_payload[cursor]

    res = fetch_channel_feed_pages(
        channel_id=0, max_behot_time=1000, max_pages=10,
        sign_fn=ok_sign, http_get=http_ok,
    )
    check("游标按响应推进 1000->900->800", requested_cursors == [1000, 900, 800],
          f"实际请求游标: {requested_cursors}")
    check("累计 4 条新闻", len(res["news"]) == 4, f"实际 {len(res['news'])}")
    check("每页都有结局记录", len(res["pages"]) == 3)
    check("全部页成功", all(p["success"] for p in res["pages"]))
    check("无游标停止", res["stop_reason"].startswith("响应未提供可用翻页游标"))
    check("未标记失败", not res["failed"])

    # 场景 2：第 2 页 HTTP 500，保留第 1 页结果，阶段记为 request
    print("场景 2: 第 2 页 HTTP 非 200，保留此前结果并停止")
    def http_fail_request(url: str, headers: Dict[str, str], timeout: int) -> Tuple[int, bytes]:
        cursor = int(url.split("max_behot_time=")[1].split("&")[0])
        if cursor == 1000:
            return 200, _make_payload([_make_item(1, 1000)], next_cursor=900)
        return 500, b"Internal Server Error"

    res = fetch_channel_feed_pages(
        channel_id=3, max_behot_time=1000, max_pages=5,
        sign_fn=ok_sign, http_get=http_fail_request,
    )
    check("保留第 1 页 1 条新闻", len(res["news"]) == 1)
    check("失败页阶段为 request", res["pages"][-1]["stage"] == STAGE_REQUEST)
    check("失败页未成功", not res["pages"][-1]["success"])
    check("失败原因含状态码", "500" in res["pages"][-1]["reason"])
    check("已标记失败", res["failed"])
    check("记录含 channel_id", res["pages"][-1]["channel_id"] == 3)

    # 场景 3：第 2 页签名失败，不得发起该页 HTTP 请求
    print("场景 3: 第 2 页签名失败，不发起 HTTP 请求并停止")
    sign_calls: List[str] = []
    http_calls: List[str] = []

    def flaky_sign(url: str, **kw: Any) -> Dict[str, Any]:
        sign_calls.append(url)
        if len(sign_calls) == 2:
            raise RuntimeError("模拟签名失败")
        return {"signed_url": url + "&a_bogus=" + "x" * 168, "a_bogus": "x" * 168}

    def http_track(url: str, headers: Dict[str, str], timeout: int) -> Tuple[int, bytes]:
        http_calls.append(url)
        return 200, _make_payload([_make_item(1, 1000)], next_cursor=900)

    res = fetch_channel_feed_pages(
        channel_id=0, max_behot_time=1000, max_pages=5,
        sign_fn=flaky_sign, http_get=http_track,
    )
    check("签名被调用 2 次（现签现发）", len(sign_calls) == 2, f"实际 {len(sign_calls)}")
    check("签名失败页未发起 HTTP 请求", len(http_calls) == 1, f"实际 HTTP {len(http_calls)} 次")
    check("失败页阶段为 sign", res["pages"][-1]["stage"] == STAGE_SIGN)
    check("保留第 1 页结果", len(res["news"]) == 1)

    # 场景 4：非法 JSON / message 非 success / data 非列表，均为 parse 阶段失败
    print("场景 4: 解析阶段三类失败均保留此前结果")
    bad_bodies = [
        b"this is not json",
        json.dumps({"message": "error", "data": []}).encode("utf-8"),
        json.dumps({"message": "success", "data": {"not": "list"}}).encode("utf-8"),
    ]
    for i, bad in enumerate(bad_bodies, 1):
        def http_bad(url: str, headers: Dict[str, str], timeout: int,
                     bad: bytes = bad) -> Tuple[int, bytes]:
            cursor = int(url.split("max_behot_time=")[1].split("&")[0])
            if cursor == 1000:
                return 200, _make_payload([_make_item(1, 1000)], next_cursor=900)
            return 200, bad

        res = fetch_channel_feed_pages(
            channel_id=0, max_behot_time=1000, max_pages=5,
            sign_fn=ok_sign, http_get=http_bad,
        )
        check(f"坏响应 {i}: 阶段为 parse 且保留 1 条",
              res["pages"][-1]["stage"] == STAGE_PARSE
              and not res["pages"][-1]["success"]
              and len(res["news"]) == 1)

    # 场景 5：脏数据条目跳过并计数，不影响其余新闻；游标重复时防死循环
    print("场景 5: 脏条目跳过计数 + 游标不前进时防重复请求")
    dirty_items = [
        _make_item(1, 1000),
        "not-a-dict",
        {"source": "无标题条目"},
        _make_item(2, 990),
    ]
    repeat_payload = _make_payload(dirty_items, next_cursor=1000)  # 游标不前进

    def http_dirty(url: str, headers: Dict[str, str], timeout: int) -> Tuple[int, bytes]:
        return 200, repeat_payload

    res = fetch_channel_feed_pages(
        channel_id=0, max_behot_time=1000, max_pages=5,
        sign_fn=ok_sign, http_get=http_dirty,
    )
    check("脏条目跳过 2 条", res["pages"][0]["skipped_count"] == 2,
          f"实际跳过 {res['pages'][0]['skipped_count']}")
    check("有效新闻 2 条", len(res["news"]) == 2)
    check("游标重复只请求 1 页", len(res["pages"]) == 1)
    check("停止原因提示游标相同", "游标与本页相同" in res["stop_reason"])

    # 场景 6：达到期望条数即停止
    print("场景 6: 达到期望条数即停止翻页")
    def http_many(url: str, headers: Dict[str, str], timeout: int) -> Tuple[int, bytes]:
        cursor = int(url.split("max_behot_time=")[1].split("&")[0])
        return 200, _make_payload(
            [_make_item(1, cursor), _make_item(2, cursor)], next_cursor=cursor - 100
        )

    res = fetch_channel_feed_pages(
        channel_id=0, max_behot_time=1000, max_pages=10, min_count=3,
        sign_fn=ok_sign, http_get=http_many,
    )
    check("累计 4 条 >= 期望 3 条后停止", len(res["news"]) == 4 and len(res["pages"]) == 2)
    check("停止原因为达到期望条数", "期望条数" in res["stop_reason"])

    print()
    if failures:
        print(f"自测未通过: {len(failures)} 项失败 -> {failures}")
        return 1
    print("全部自测场景通过 ✔")
    return 0


def _print_page_records(pages: List[Dict[str, Any]]) -> None:
    print("\n---------------- 逐页结局记录 ----------------")
    for idx, p in enumerate(pages, 1):
        mark = "✔" if p["success"] else "✘"
        print(
            f"[第{idx}页] {mark} channel_id={p['channel_id']} "
            f"max_behot_time={p['max_behot_time']} 阶段={p['stage']} "
            f"本页={p['page_count']} 跳过={p['skipped_count']} 累计={p['total_count']}"
        )
        if not p["success"]:
            print(f"        失败原因: {p['reason']}")
    print("----------------------------------------------")


def main() -> int:
    parser = argparse.ArgumentParser(description="今日头条主页新闻流抓取复现工具（支持按频道连续翻页）")
    parser.add_argument("--channel-id", type=int, default=0, help="频道 ID，默认 0（推荐）")
    parser.add_argument("--max-pages", type=int, default=1, help="最多翻页数，默认 1")
    parser.add_argument("--min-count", type=int, default=None, help="期望累计条数，达到即停止")
    parser.add_argument("--max-behot-time", type=int, default=None, help="起始翻页游标，默认当前时间")
    parser.add_argument("--category", type=str, default="pc_profile_recommend", help="分类标识")
    parser.add_argument("--timeout", type=int, default=15, help="单页请求超时（秒）")
    parser.add_argument("--limit", type=int, default=10, help="展示的新闻条数限制")
    parser.add_argument("--save", action="store_true", default=True, help="是否将结果保存至 sample_data.json")
    parser.add_argument("--self-test", action="store_true", help="运行本地样例自测（不联网）")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    result = fetch_channel_feed_pages(
        channel_id=args.channel_id,
        max_behot_time=args.max_behot_time,
        max_pages=args.max_pages,
        min_count=args.min_count,
        category=args.category,
        timeout=args.timeout,
    )
    news = result["news"]

    _print_page_records(result["pages"])
    print(f"\n汇总: 频道 {result['channel_id']} 共解析 {len(news)} 条，"
          f"翻页 {len(result['pages'])} 页，停止原因: {result['stop_reason']}")

    if news:
        print(f"\n================ 实时抓取结果 (共 {len(news)} 条) ================")
        for idx, item in enumerate(news[: args.limit], 1):
            print(f"\n[{idx}] 标题: {item['title']}")
            print(f"    来源: {item['source']} | 时间: {item['publish_time']} | 阅读: {item['read_count']} | 评论: {item['comments_count']}")
            print(f"    链接: {item['url']}")
        print("=================================================================\n")

        if args.save:
            SAMPLE_OUTPUT.write_text(
                json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info("完整数据已落盘至: %s", SAMPLE_OUTPUT.name)

    if result["failed"]:
        logger.error("本次采集存在失败页（已保留此前解析结果）")
        return 1
    if not news:
        logger.error("未获取到新闻数据（可能由于签名过期或风控拦截）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
