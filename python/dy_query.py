#!/usr/bin/env python3
"""Offset + search_id capable search adapter built on dy-cli's own client.

The upstream CLI exposes ``dy search`` without an offset flag, and Douyin's
search endpoint requires chaining the previous page's ``extra.logid`` as
``search_id`` — a naive offset-only second page returns an empty list. This
module adds exactly those two pieces of argument plumbing on top of
``dy_cli``'s client; signing, cookies, retries, and rate limiting stay in
dy-cli. No X-Bogus logic is reimplemented here.
"""
from __future__ import annotations

import argparse

from dy_cli.engines.api_client import SEARCH_URL, DouyinAPIClient, DouyinAPIError
from dy_cli.utils.signature import get_base_params

from dy_paging import classify_error, emit_json, error_paging, paging_from_result

SORT_MAP = {"综合": 0, "最多点赞": 1, "最新发布": 2, "general": 0, "most_like": 1, "latest": 2}
TIME_MAP = {"不限": 0, "一天内": 1, "一周内": 7, "半年内": 182}

# Kept for callers/tests that imported these from here before dy_paging existed.
classify = classify_error


def emit(data) -> None:
    emit_json({"ok": True, "schema_version": "1", "data": data})


def fail(code: str, message: str, **extra) -> int:
    emit_json({
        "ok": False, "schema_version": "1",
        "error": {"code": code, "message": message},
        **extra,
    })
    return 1


def search_with_logid(client: DouyinAPIClient, keyword: str, count: int,
                      offset: int, sort: str, publish_time: str, logid: str) -> dict:
    """Same request dy-cli makes, plus the documented search_id chain field."""
    params = {
        **get_base_params(),
        "keyword": keyword,
        "search_channel": "general",
        "sort_type": str(SORT_MAP.get(sort, 0)),
        "publish_time": str(TIME_MAP.get(publish_time, 0)),
        "filter_duration": "0",
        "offset": str(max(0, offset)),
        "count": str(max(1, min(20, count))),
        "search_source": "normal_search",
        "query_correct_type": "1",
        "is_filter_search": "0",
        "search_id": logid,
    }
    data = client._get(SEARCH_URL, params=params)
    if data.get("status_code") != 0:
        raise DouyinAPIError(f"搜索失败: {data.get('status_msg', 'unknown error')}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search")
    search.add_argument("keyword")
    search.add_argument("--count", type=int, default=10)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--sort", default="综合")
    search.add_argument("--time", dest="publish_time", default="不限")
    search.add_argument("--type", dest="search_type", default="general")
    search.add_argument("--logid", default="")
    args = parser.parse_args()

    count = max(1, min(20, int(args.count or 10)))
    offset = max(0, int(args.offset or 0))

    client = DouyinAPIClient.from_config()
    try:
        if args.command != "search":
            return fail("invalid_args", f"unknown command: {args.command}",
                        paging=error_paging(offset=offset, count=count, outcome="invalid_args"))
        if args.logid:
            result = search_with_logid(
                client, args.keyword, count, offset, args.sort,
                args.publish_time, args.logid,
            )
        else:
            result = client.search(
                keyword=args.keyword,
                sort_type=SORT_MAP.get(args.sort, 0),
                publish_time=TIME_MAP.get(args.publish_time, 0),
                search_type=args.search_type,
                offset=offset,
                count=count,
            )
        page = paging_from_result(result, offset=offset, count=count)
        if page["outcome"] == "challenge_required":
            # 已经解析出来的条目一起带回去：中止不等于丢数据。
            return fail(
                "challenge_required",
                "抖音搜索返回 verify_check：当前账号需要人机验证。插件不会自动绕过；"
                "请在专用 Edge 窗口手动完成验证后重试。",
                paging=page,
                data={"result": result},
            )
        emit({
            "result": result,
            "offset": page["offset"],
            "count": count,
            "has_more": page["has_more"],
            "next_offset": page["next_offset"],
            "logid": page["logid"],
            "items": page["items"],
            "outcome": page["outcome"],
            "search_id_used": args.logid,
            "paging": page,
        })
        return 0
    except DouyinAPIError as exc:
        outcome = classify_error(str(exc))
        return fail(outcome, str(exc),
                    paging=error_paging(offset=offset, count=count, outcome=outcome))
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
