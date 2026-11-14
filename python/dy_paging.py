#!/usr/bin/env python3
"""Shared search-pagination helpers (dependency-free).

Douyin's search endpoint chains pages through the previous page's
``extra.logid`` (sent back as ``search_id``).  Moving only ``offset`` without
that credential is what makes the second page come back empty.  Both the HTTP
service and the small ``dy_query`` adapter need to describe a page the same
way, so the classification and "what does this page mean" logic lives here —
without importing ``dy_cli``, FastAPI, or anything else heavyweight.

Outcome vocabulary (also mirrored by the Node collector):

  completed            page fetched normally; upstream says more may exist
  no_more              upstream explicitly says there are no more results
  empty_page           zero usable rows but upstream did NOT say "no more"
                       (never report this as no_more — that would be a lie)
  challenge_required   verify_check / captcha; stop, do not bypass
  rate_limited         HTTP 429 / 频繁 / too many
  not_authenticated    login missing or expired
  parse_failed         upstream body could not be parsed
  not_found            upstream said the query/item does not exist
  backend_unavailable  anything else (process failed, timeout, empty body…)
  invalid_args         caller supplied a malformed request
"""
from __future__ import annotations

import json
import re
import sys

# Outcomes that mean "stop the chain now and tell the caller why".
ABORT_OUTCOMES = frozenset({
    "challenge_required",
    "rate_limited",
    "not_authenticated",
    "parse_failed",
})

# Errors we know how to name; anything else becomes backend_unavailable.
ERROR_OUTCOMES = ABORT_OUTCOMES | frozenset({
    "not_found",
    "backend_unavailable",
    "invalid_args",
})


def classify_error(message, code: str = "") -> str:
    """Map an upstream/driver message (or an explicit code) onto an outcome."""
    code = str(code or "").strip()
    if code in ERROR_OUTCOMES:
        return code
    text = str(message or "")
    if re.search(r"请先登录|未登录|登录已失效|NEED_LOGIN", text, re.I):
        return "not_authenticated"
    if re.search(r"验证码|verify|challenge|安全验证", text, re.I):
        return "challenge_required"
    if re.search(r"429|限流|频繁|too many", text, re.I):
        return "rate_limited"
    if re.search(r"不存在|未找到|无数据|数据为空", text):
        return "not_found"
    if re.search(r"JSON|解析", text, re.I):
        return "parse_failed"
    return "backend_unavailable"


def emit_json(envelope: dict) -> None:
    """Write one JSON object to stdout as UTF-8 regardless of console code page."""
    payload = json.dumps(envelope, ensure_ascii=False) + "\n"
    try:
        sys.stdout.buffer.write(payload.encode("utf-8"))
        sys.stdout.buffer.flush()
    except AttributeError:  # 被测试/嵌入方换成了 StringIO
        sys.stdout.write(payload)
        sys.stdout.flush()


def _bool_or_none(value):
    """``has_more`` is 0/1/true/false upstream; missing means unknown."""
    if value is None:
        return None
    return bool(value)


def paging_from_result(result, *, offset: int, count: int) -> dict:
    """Describe one raw search page: what the caller must pass next."""
    if not isinstance(result, dict):
        result = {"data": []}
    rows = result.get("data")
    if not isinstance(rows, list):
        rows = []

    extra = result.get("extra")
    logid = ""
    if isinstance(extra, dict):
        logid = str(extra.get("logid") or "")
    if not logid:
        logid = str(result.get("logid") or "")

    raw_more = result.get("has_more")
    has_more = _bool_or_none(raw_more)

    nil_info = result.get("search_nil_info")
    nil_type = ""
    if isinstance(nil_info, dict):
        nil_type = str(nil_info.get("search_nil_type") or "")

    if nil_type == "verify_check":
        outcome = "challenge_required"
    elif has_more is False:
        outcome = "no_more"
    elif not rows:
        # Empty with has_more true/unknown: this is the exact failure mode the
        # pagination chain is meant to avoid.  Never call it "finished".
        outcome = "empty_page"
    else:
        outcome = "completed"

    offset = max(0, int(offset or 0))
    return {
        "offset": offset,
        "count": len(rows),
        "requested_count": int(count),
        "items": len(rows),
        "next_offset": offset + len(rows),
        "logid": logid,
        "has_more": has_more,
        "outcome": outcome,
    }


def error_paging(*, offset: int, count: int, outcome: str) -> dict:
    """Paging metadata for a page that failed before yielding rows."""
    offset = max(0, int(offset or 0))
    return {
        "offset": offset,
        "count": 0,
        "requested_count": int(count),
        "items": 0,
        "next_offset": offset,
        "logid": "",
        "has_more": None,
        "outcome": outcome,
    }
