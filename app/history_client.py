"""WeChat MP history list via profile_ext?action=getmsg (same as Schinza).

Requires short-lived credentials: __biz, uin, key, pass_ticket (recommended).
"""

from __future__ import annotations

import html
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

import requests

from app.errors import describe_exception, wechat_error_hint
from app.history_ranges import range_text

GETMSG_URL = "https://mp.weixin.qq.com/mp/profile_ext"
REQUIRED_CRED_KEYS = ("__biz", "uin", "key")

# getmsg ``count`` is number of *push messages* (推送), not articles.
# Busy accounts may push many times/day; keep paging until date cutoff.
DEFAULT_PAGE_COUNT = 8
DEFAULT_MAX_PAGES = 100

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "WindowsWechat(0x63090a13) XWEB/11275"
)


def _fully_unquote(value: str) -> str:
    s = value or ""
    for _ in range(3):
        n = unquote(s)
        if n == s:
            break
        s = n
    return s


def normalize_credentials(cred: dict[str, Any]) -> dict[str, Any]:
    out = dict(cred)
    for k in (
        "__biz",
        "uin",
        "key",
        "pass_ticket",
        "appmsg_token",
        "wxtoken",
        "user_agent",
        "devicetype",
        "clientversion",
        "slave_sid",
        "data_ticket",
    ):
        if k in out and isinstance(out[k], str):
            out[k] = _fully_unquote(out[k].strip())
    return out


def validate_credentials(cred: dict[str, Any]) -> tuple[bool, str]:
    missing = [k for k in REQUIRED_CRED_KEYS if not str(cred.get(k) or "").strip()]
    if missing:
        return False, f"缺少字段: {', '.join(missing)}"
    return True, ""


def _network_error(exc: Exception) -> str:
    base = describe_exception(exc)
    if isinstance(exc, requests.Timeout):
        return base + "（连接微信超时，请检查网络后重试）"
    if isinstance(exc, requests.exceptions.SSLError):
        return base + "（SSL 错误，可能是证书或代理问题）"
    return base + "（网络错误，请检查网络/代理设置后重试）"


def _getmsg_debug_path() -> Path:
    raw = os.environ.get("SCHINZA_GETMSG_DEBUG") or ""
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1] / "data" / "getmsg_debug.log"


def _debug_log(line: str) -> None:
    """Append a line to data/getmsg_debug.log (抓取诊断，供排查漏抓)。"""
    try:
        path = _getmsg_debug_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S") + " " + line + "\n")
    except Exception:
        pass


def _clean_url(raw: str) -> str:
    s = html.unescape((raw or "").strip())
    s = s.replace("\\/", "/")
    s = s.replace("\\u0026", "&").replace("\\x26", "&")
    s = s.replace("&amp;", "&")
    if s.startswith("//"):
        s = "https:" + s
    if s.startswith("http://mp.weixin.qq.com"):
        s = "https://" + s[len("http://") :]
    return s


def _link_from_item(item: dict[str, Any]) -> str:
    for key in (
        "content_url",
        "content_url_encoded",
        "url",
        "link",
        "content_url_with_token",
    ):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return _clean_url(raw)
    return ""


def _mid_idx_sn(link: str) -> tuple[str, str, str]:
    link = _clean_url(link)
    if not link:
        return "", "", ""
    try:
        q = parse_qs(urlparse(link).query)
    except Exception:
        return "", "", ""
    mid = (q.get("mid") or q.get("appmsgid") or [""])[0]
    idx = (q.get("idx") or q.get("itemidx") or [""])[0]
    sn = (q.get("sn") or [""])[0]
    return str(mid or ""), str(idx or ""), str(sn or "")


def article_identity(
    link: str,
    *,
    title: str = "",
    publish_ts: int = 0,
    msg_id: str = "",
    ordinal: int = 0,
) -> str:
    """Stable identity for dedupe.

    Prefer mid+idx (same push, different articles). Never collapse two articles
    that only share __biz / bare /s path.
    """
    link = _clean_url(link)
    mid, idx, sn = _mid_idx_sn(link)
    if mid:
        # idx distinguishes 同一条多图文里的第 1/2/… 篇
        return f"mid:{mid}|idx:{idx or str(ordinal or 1)}|sn:{sn}"

    if link:
        try:
            u = urlparse(link)
        except Exception:
            u = None
        if u is not None:
            m = re.search(r"/s/([A-Za-z0-9_-]+)", u.path or "")
            if m:
                return f"s:{m.group(1)}"
            # Keep full URL path+query (minus fragment) — do not over-strip
            return urlunparse((u.scheme, u.netloc, u.path, "", u.query, ""))

    # Last resort: never merge different titles from the same push
    return f"msg:{msg_id}|ord:{ordinal}|ts:{publish_ts}|t:{title}"


def _item_to_row(
    item: dict[str, Any],
    publish_ts: int,
    *,
    msg_id: str = "",
    ordinal: int = 0,
) -> dict[str, Any] | None:
    title = (item.get("title") or "").strip()
    link = _link_from_item(item)
    # 头条偶发缺 link：只要有标题也保留，避免「同一天多图文只剩最后一篇」
    if not link and not title:
        _debug_log(
            "DROP item no-link-no-title "
            + json.dumps(
                {k: v for k, v in item.items() if k in ("title", "content_url", "url", "mid", "idx", "sn")},
                ensure_ascii=False,
            )
        )
        return None
    if not title:
        title = "(无标题)"
    mid, idx, sn = _mid_idx_sn(link)
    return {
        "title": title,
        "link": link,
        "digest": (item.get("digest") or "").strip(),
        "cover": _clean_url(item.get("cover") or item.get("cdn_url") or ""),
        "author": (item.get("author") or "").strip(),
        "publish_ts": publish_ts,
        "publish_at": (
            datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d %H:%M")
            if publish_ts
            else ""
        ),
        "mid": mid,
        "idx": idx or str(ordinal or 1),
        "sn": sn,
        "identity": article_identity(
            link,
            title=title,
            publish_ts=publish_ts,
            msg_id=msg_id,
            ordinal=ordinal,
        ),
    }


def _iter_msg_article_items(msg: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """Yield (ordinal, item) for head + multi-app articles in one push.

    WeChat layout:
      - 第 1 篇在 app_msg_ext_info
      - 第 2..N 篇在 app_msg_ext_info.multi_app_msg_item_list
    Some payloads also put multi on the message root — include those too.
    """
    app = msg.get("app_msg_ext_info") or {}
    if not isinstance(app, dict):
        app = {}

    items: list[tuple[int, dict[str, Any]]] = []
    if app:
        # ordinal 1 = 头条
        items.append((1, app))

    multi = app.get("multi_app_msg_item_list")
    if not isinstance(multi, list):
        multi = []
    # Fallback: multi list wrongly nested at message root (seen in some scrapers)
    root_multi = msg.get("multi_app_msg_item_list")
    if isinstance(root_multi, list) and root_multi:
        multi = list(multi) + [x for x in root_multi if x not in multi]

    for i, sub in enumerate(multi):
        if isinstance(sub, dict):
            items.append((i + 2, sub))
    return items


def parse_general_msg_list(payload: dict[str, Any] | str) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        payload = json.loads(payload) if payload.strip() else {}
    rows: list[dict[str, Any]] = []
    for msg in payload.get("list") or []:
        if not isinstance(msg, dict):
            continue
        comm = msg.get("comm_msg_info") or {}
        if not isinstance(comm, dict):
            comm = {}
        # type 49 = 图文；缺省时只要有 app_msg_ext_info 也尝试解析
        msg_type = comm.get("type")
        if msg_type is not None and str(msg_type) not in ("49", "49.0"):
            # 仍可能有图文扩展，不直接跳过
            pass
        publish_ts = int(comm.get("datetime") or 0)
        msg_id = str(comm.get("id") or "")

        app = msg.get("app_msg_ext_info")
        root_multi = msg.get("multi_app_msg_item_list")
        has_root_multi = isinstance(root_multi, list) and bool(root_multi)
        if (not isinstance(app, dict) or not app) and not has_root_multi:
            # 有些 payload 把多图文子文章放在消息根级 multi_app_msg_item_list；
            # 若 app_msg_ext_info 缺失但根级有文章，不能整条丢弃（否则漏抓）。
            _debug_log(
                "SKIP push "
                + json.dumps(
                    {
                        "id": msg.get("comm_msg_info", {}).get("id")
                        if isinstance(msg.get("comm_msg_info"), dict)
                        else None,
                        "type": msg.get("comm_msg_info", {}).get("type")
                        if isinstance(msg.get("comm_msg_info"), dict)
                        else None,
                        "keys": sorted(msg.keys()),
                        "title": (msg.get("app_msg_ext_info") or {}).get("title")
                        if isinstance(msg.get("app_msg_ext_info"), dict)
                        else None,
                    },
                    ensure_ascii=False,
                )
            )
            continue

        for ordinal, item in _iter_msg_article_items(msg):
            row = _item_to_row(
                item,
                publish_ts,
                msg_id=msg_id,
                ordinal=ordinal,
            )
            if row:
                rows.append(row)
    return rows


def parse_getmsg_response(payload: dict[str, Any]) -> dict[str, Any]:
    ret = payload.get("ret")
    errmsg = str(payload.get("errmsg") or "")
    if ret not in (0, "0") and errmsg != "ok":
        return {
            "ok": False,
            "error": wechat_error_hint(ret, errmsg),
            "articles": [],
            "can_continue": False,
            "next_offset": None,
            "raw": payload,
        }
    gml = payload.get("general_msg_list") or ""
    if isinstance(gml, dict):
        articles = parse_general_msg_list(gml)
    else:
        articles = parse_general_msg_list(str(gml))
    can = payload.get("can_msg_continue")
    return {
        "ok": True,
        "error": "",
        "articles": articles,
        "can_continue": bool(int(can)) if can is not None and str(can).isdigit() else bool(can),
        "next_offset": payload.get("next_offset"),
        "msg_count": payload.get("msg_count"),
        "raw": payload,
    }


def _get_page_with_retry(
    sess: requests.Session,
    *,
    params: dict[str, str],
    headers: dict[str, str],
    cookies: dict[str, str],
    timeout: float,
    retries: int,
    retry_delay_s: float,
) -> tuple[requests.Response | None, Exception | None]:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = sess.get(
                GETMSG_URL, params=params, headers=headers, cookies=cookies, timeout=timeout
            )
            return resp, None
        except requests.exceptions.SSLError as exc:
            # 证书类错误重试无意义：立即返回，避免反复超时
            return None, exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(retry_delay_s * (attempt + 1))
        except Exception as exc:  # noqa: BLE001 - other errors: surface immediately
            return None, exc
    return None, last_exc


def fetch_getmsg_page(
    cred: dict[str, Any],
    *,
    offset: int = 0,
    count: int = DEFAULT_PAGE_COUNT,
    timeout: float = 25.0,
    session: requests.Session | None = None,
    retries: int = 2,
    retry_delay_s: float = 1.0,
) -> dict[str, Any]:
    cred = normalize_credentials(cred)
    ok, err = validate_credentials(cred)
    if not ok:
        return {
            "ok": False,
            "error": err,
            "articles": [],
            "can_continue": False,
            "next_offset": None,
        }

    params = {
        "action": "getmsg",
        "__biz": str(cred["__biz"]).strip(),
        "f": "json",
        "offset": str(offset),
        "count": str(count),
        "is_ok": "1",
        "scene": "124",
        "uin": str(cred["uin"]).strip(),
        "key": str(cred["key"]).strip(),
        "wxtoken": str(cred.get("wxtoken") or ""),
        "devicetype": str(cred.get("devicetype") or ""),
        "clientversion": str(cred.get("clientversion") or "0"),
        "x5": "0",
    }
    if cred.get("pass_ticket"):
        params["pass_ticket"] = str(cred["pass_ticket"]).strip()
    if cred.get("appmsg_token"):
        params["appmsg_token"] = str(cred["appmsg_token"]).strip()

    headers = build_getmsg_headers(cred, biz=params["__biz"])
    cookies = build_getmsg_cookies(cred)

    sess = session or requests.Session()
    # Bypass system MITM proxy — talk to WeChat directly.
    sess.trust_env = False
    resp, net_err = _get_page_with_retry(
        sess,
        params=params,
        headers=headers,
        cookies=cookies,
        timeout=timeout,
        retries=max(0, int(retries)),
        retry_delay_s=max(0.0, float(retry_delay_s)),
    )
    if net_err is not None:
        return {
            "ok": False,
            "error": _network_error(net_err),
            "articles": [],
            "can_continue": False,
            "next_offset": None,
        }
    text = resp.text.strip()
    if text.startswith("{"):
        payload = resp.json()
    else:
        try:
            payload = json.loads(text)
        except Exception:
            return {
                "ok": False,
                "error": f"非 JSON 响应 status={resp.status_code} body[:160]={text[:160]!r}",
                "articles": [],
                "can_continue": False,
                "next_offset": None,
            }
    page = parse_getmsg_response(payload)
    page["http_status"] = resp.status_code
    page["request_url"] = f"{GETMSG_URL}?{urlencode(params)}"
    return page


ProgressCb = Callable[[str], None]


def _page_newest_ts(batch: list[dict[str, Any]]) -> int:
    newest = 0
    for a in batch:
        ts = int(a.get("publish_ts") or 0)
        if ts > newest:
            newest = ts
    return newest


def merge_resumed_articles(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _dedupe(list(existing or []) + list(incoming or []))


def oldest_publish_ts(articles: list[dict[str, Any]]) -> int | None:
    stamps = [int(a.get("publish_ts") or 0) for a in (articles or [])]
    stamps = [ts for ts in stamps if ts > 0]
    return min(stamps) if stamps else None


def _article_key(a: dict[str, Any]) -> str:
    """Dedupe key shared by getmsg rows and merged sighting rows."""
    mid = str(a.get("mid") or "")
    idx = str(a.get("idx") or "")
    sn = str(a.get("sn") or "")
    if mid:
        return f"mid:{mid}|idx:{idx or '1'}|sn:{sn}"
    key = str(a.get("identity") or "") or article_identity(
        str(a.get("link") or ""),
        title=str(a.get("title") or ""),
        publish_ts=int(a.get("publish_ts") or 0),
    )
    if not key:
        key = f"t:{a.get('title')}|ts:{a.get('publish_ts')}|l:{a.get('link')}"
    return key


def _dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for a in articles:
        key = _article_key(a)
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def plan_history_resume(
    *,
    biz: str,
    resume_biz: str = "",
    resume_offset: int | None = None,
    resume_oldest_ts: int | None = None,
    days: int | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict[str, Any]:
    """Decide where a (possibly resumed) getmsg scan starts.

    Returns ``start_offset`` (0 = newest, >0 = continue after a page cap) plus
    why we chose it, so callers can tell "continued last progress" apart from
    "started over". A resume is dropped when the account changed or when the
    previously scanned window already reaches past this run's lower bound.
    """
    try:
        offset = int(resume_offset or 0)
    except (TypeError, ValueError):
        offset = 0
    plan: dict[str, Any] = {
        "start_offset": 0,
        "resume_biz": (resume_biz or "").strip(),
        "resume_offset": offset,
        "resume_oldest_ts": resume_oldest_ts,
        "restarted": False,
        "resumed": False,
        "reason": "no_resume",
    }
    if offset <= 0:
        return plan
    if (biz or "").strip() != (resume_biz or "").strip():
        plan["restarted"] = True
        plan["reason"] = "biz_changed"
        return plan

    range_mode = start_ts is not None or end_ts is not None
    need_older_than: int | None = None
    if not range_mode and days is None:
        # 「全部」：一直往更早翻，续翻永远有意义。
        plan["start_offset"] = offset
        plan["resumed"] = True
        plan["reason"] = "same_account"
        return plan
    if start_ts is not None:
        need_older_than = int(start_ts)
    elif days is not None:
        need_older_than = int(time.time()) - max(1, int(days)) * 86400
    if need_older_than is None or resume_oldest_ts is None:
        plan["start_offset"] = offset
        plan["resumed"] = True
        plan["reason"] = "same_account"
        return plan
    try:
        oldest = int(resume_oldest_ts)
    except (TypeError, ValueError):
        oldest = 0
    if oldest > 0 and oldest <= need_older_than:
        # 上次已扫描到本次时间范围下界（或更早）：旧 offset 只会翻到范围之外，
        # 续翻没有意义，按新窗口从头开始。
        plan["restarted"] = True
        plan["reason"] = "older_than_range_start"
        return plan
    plan["start_offset"] = offset
    plan["resumed"] = True
    plan["reason"] = "same_account"
    return plan


def resolve_history_start_offset(
    *,
    biz: str,
    resume_biz: str = "",
    resume_offset: int | None = None,
    resume_oldest_ts: int | None = None,
    days: int | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> int:
    """Where to start getmsg paging. 0 = newest; >0 = continue after a 100-page cap."""
    return int(
        plan_history_resume(
            biz=biz,
            resume_biz=resume_biz,
            resume_offset=resume_offset,
            resume_oldest_ts=resume_oldest_ts,
            days=days,
            start_ts=start_ts,
            end_ts=end_ts,
        )["start_offset"]
    )


_RESUME_BIZ_KEYS = ("biz", "__biz")
_RESUME_OFFSET_KEYS = ("next_offset", "offset", "start_offset")
_RESUME_OLDEST_KEYS = ("oldest_ts", "scanned_oldest_ts")
_RESUME_SIGHTING_KEYS = (
    "sighting_keys",
    "merged_sighting_keys",
    "sighting_identities",
)


def resume_plan_from_state(
    resume: dict[str, Any] | None,
    *,
    biz: str,
    resume_biz: str = "",
    resume_offset: int | None = None,
    resume_oldest_ts: int | None = None,
    days: int | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict[str, Any]:
    """Merge a previous ``resume`` payload with explicit args, then plan.

    Explicit arguments win over fields carried in ``resume``.
    """
    state = dict(resume or {})

    def _pick(keys: tuple[str, ...]) -> Any:
        for k in keys:
            if k in state and state[k] is not None:
                return state[k]
        return None

    if not (resume_biz or "").strip():
        resume_biz = str(_pick(_RESUME_BIZ_KEYS) or "")
    from_state = resume_offset is None
    if from_state:
        resume_offset = _pick(_RESUME_OFFSET_KEYS)
    if resume_oldest_ts is None:
        resume_oldest_ts = _pick(_RESUME_OLDEST_KEYS)
    if from_state and state.get("can_continue") is False:
        # A previous run that ended for good (finished/empty page) must not be
        # resurrected as a resume just because it remembers its final offset.
        resume_offset = 0
    plan = plan_history_resume(
        biz=biz,
        resume_biz=resume_biz,
        resume_offset=resume_offset,
        resume_oldest_ts=resume_oldest_ts,
        days=days,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    plan["sighting_keys"] = [
        str(k) for k in (_pick(_RESUME_SIGHTING_KEYS) or []) if str(k)
    ]
    return plan


def _sighting_to_article(s: dict[str, Any]) -> dict[str, Any] | None:
    link = _clean_url(str(s.get("link") or ""))
    title = str(s.get("title") or "").strip() or "(无标题)"
    if not link and title == "(无标题)":
        return None
    publish_ts = int(s.get("publish_ts") or 0)
    mid, idx, sn = _mid_idx_sn(link)
    if not mid:
        mid = str(s.get("mid") or "")
        idx = str(s.get("idx") or idx or "1")
        sn = str(s.get("sn") or sn)
    identity = str(s.get("identity") or "") or article_identity(
        link, title=title, publish_ts=publish_ts
    )
    return {
        "title": title,
        "link": link,
        "digest": str(s.get("digest") or "").strip(),
        "cover": _clean_url(str(s.get("cover") or "")),
        "author": str(s.get("author") or "").strip(),
        "publish_ts": publish_ts,
        "publish_at": str(s.get("publish_at") or "")
        or (
            datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d %H:%M")
            if publish_ts
            else ""
        ),
        "mid": mid,
        "idx": idx or "1",
        "sn": sn,
        "identity": identity,
        "source": str(s.get("source") or "sighting"),
    }


def merge_articles_with_sightings(
    base: list[dict[str, Any]],
    sightings: list[dict[str, Any]],
    *,
    cutoff_ts: int = 0,
    biz: str = "",
    collect_sighting_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Merge getmsg articles with MITM/manual sightings (fills API gaps).

    WeChat often omits later same-day pushes from ``getmsg``. Sightings collected
    while browsing (or补录) are merged in and deduped by mid|idx|sn / identity.

    ``collect_sighting_keys`` collects every sighting key that contributed a row,
    so a later (resumed) fetch can avoid counting the same补录 twice while still
    merging it into the visible article list.
    """
    rows: list[dict[str, Any]] = []
    for a in base or []:
        row = dict(a)
        row.setdefault("source", "getmsg")
        rows.append(row)

    biz = (biz or "").strip()
    for s in sightings or []:
        if biz:
            sb = str(s.get("__biz") or "").strip()
            link_biz = ""
            try:
                q = parse_qs(urlparse(_clean_url(str(s.get("link") or ""))).query)
                link_biz = unquote((q.get("__biz") or [""])[0])
            except Exception:
                link_biz = ""
            if sb and sb != biz:
                continue
            if not sb and link_biz and link_biz != biz:
                continue
        art = _sighting_to_article(s)
        if not art:
            continue
        ts = int(art.get("publish_ts") or 0)
        if cutoff_ts and ts and ts < cutoff_ts:
            continue
        if collect_sighting_keys is not None:
            # Same key space as ``_article_key`` so a later resume can compare.
            collect_sighting_keys.add(_article_key(art))
        rows.append(art)

    merged = _dedupe(rows)
    merged = [
        a
        for a in merged
        if not cutoff_ts
        or not int(a.get("publish_ts") or 0)
        or int(a.get("publish_ts") or 0) >= cutoff_ts
    ]
    merged.sort(key=lambda a: int(a.get("publish_ts") or 0), reverse=True)
    return merged


def _count_new_sightings(
    merged: list[dict[str, Any]],
    base: list[dict[str, Any]],
    *,
    seen_sighting_keys: set[str] | None = None,
) -> int:
    """How many merged rows came from补录/抓包 and were not counted before.

    ``seen_sighting_keys`` holds keys an earlier (resumed) fetch already
    reported, so continuing after a page cap does not count the same补录 again.
    """
    seen = seen_sighting_keys or set()
    base_keys = {_article_key(a) for a in _dedupe(base)}
    extra = 0
    for row in merged:
        key = _article_key(row)
        if key in base_keys:
            continue
        if key in seen:
            continue
        extra += 1
    return max(0, extra)


def is_rate_limit_error(err: str) -> bool:
    low = (err or "").lower()
    return any(
        k in low
        for k in ("freq", "频繁", "操作频繁", "unknownerror", "风控")
    )


def classify_stopped_reason(
    err: str, *, cancelled: bool = False, ok: bool = False
) -> str:
    if cancelled:
        return "cancelled"
    if ok:
        return "completed"
    if is_rate_limit_error(err):
        return "rate_limited"
    low = (err or "").lower()
    if any(k in low for k in ("invalid", "失效", "过期", "缺少字段")):
        return "expired"
    return "network"


def build_getmsg_headers(cred: dict[str, Any], *, biz: str) -> dict[str, str]:
    ua = str(cred.get("user_agent") or "").strip() or DEFAULT_UA
    return {
        "User-Agent": ua,
        "Referer": (
            f"https://mp.weixin.qq.com/mp/profile_ext?action=home"
            f"&__biz={biz}&scene=124#wechat_redirect"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }


def build_getmsg_cookies(cred: dict[str, Any]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if cred.get("pass_ticket"):
        cookies["pass_ticket"] = str(cred["pass_ticket"]).strip()
    if cred.get("uin"):
        cookies["wxuin"] = str(cred["uin"]).strip()
    if cred.get("slave_sid"):
        cookies["slave_sid"] = str(cred["slave_sid"]).strip()
    if cred.get("data_ticket"):
        cookies["data_ticket"] = str(cred["data_ticket"]).strip()
    return cookies


def _rate_limit_hint(err: str) -> str:
    if is_rate_limit_error(err):
        return "（疑似被微信限流：建议暂停几分钟，降低拉取频率后再试）"
    return ""


def fetch_history_days(
    cred: dict[str, Any],
    *,
    days: int | None = 7,
    max_pages: int = DEFAULT_MAX_PAGES,
    count: int = DEFAULT_PAGE_COUNT,
    sleep_s: float = 3.4,
    sleep_jitter: float = 0.32,
    cooldown_every: int = 4,
    cooldown_extra_s: float = 10.0,
    timeout: float = 20.0,
    on_progress: ProgressCb | None = None,
    sightings: list[dict[str, Any]] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    start_offset: int = 0,
    resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Paginate getmsg and keep articles with publish_ts within the last ``days``.

    ``days=None`` 表示「全部」：不过滤日期，一直翻页到没有更多（或达 max_pages）。

    Important: getmsg ``count`` is push-message count. One push may contain many
    articles (multi-appmsg). We keep paging until the whole page is older than
    the cutoff (or WeChat says no more / hit max_pages).

    ``sightings`` (MITM browse / 补录) are merged in because WeChat often omits
    later same-day pushes from getmsg alone.

    Resuming after a page cap: pass the previous result back as ``resume`` (or
    explicitly ``start_offset``). A stale resume — different ``__biz``, or
    already scanned past this run's lower bound — is dropped and the scan
    restarts at offset 0. The result always carries everything the next call
    needs: ``resume`` (start offset, oldest scanned ts, ``__biz``, merged
    article count, already-counted sighting keys) plus the legacy flat fields.
    """
    cred = normalize_credentials(cred)
    ok, err = validate_credentials(cred)
    if not ok:
        return {
            "ok": False,
            "error": err,
            "articles": [],
            "pages": 0,
            "stopped_reason": classify_stopped_reason(err),
        }

    cutoff: int | None = None
    upper_ts: int | None = None
    range_mode = start_ts is not None or end_ts is not None
    if range_mode:
        # 日期区间模式：start_ts ~ end_ts（含当日）
        days = None
        cutoff = int(start_ts) if start_ts is not None else None
        upper_ts = int(end_ts) if end_ts is not None else None
    elif days is not None:
        days = max(1, int(days))
        cutoff = int(time.time()) - days * 86400
    if range_mode:
        _parts = []
        if cutoff is not None:
            _parts.append(datetime.fromtimestamp(cutoff).strftime("%Y-%m-%d"))
        if upper_ts is not None:
            _parts.append(datetime.fromtimestamp(upper_ts).strftime("%Y-%m-%d"))
        scope = " 至 ".join(_parts) if _parts else "全部历史"
    else:
        scope = "全部历史" if days is None else f"近 {days} 天"
    articles: list[dict[str, Any]] = []
    pages = 0
    biz = str(cred.get("__biz") or "").strip()
    # Decide resume before touching the network: the same rule the UI shows
    # ("continued last progress" vs "started over"). A ``resume`` payload from
    # an earlier page-cap result wins over the legacy explicit ``start_offset``,
    # because only it knows which __biz/time window that offset belonged to.
    if resume:
        resume_info = resume_plan_from_state(
            resume,
            biz=biz,
            resume_offset=start_offset or None,
            days=days,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    else:
        try:
            legacy_offset = max(0, int(start_offset or 0))
        except (TypeError, ValueError):
            legacy_offset = 0
        resume_info = {
            "start_offset": legacy_offset,
            "restarted": False,
            "resumed": legacy_offset > 0,
            "reason": "explicit_start_offset" if legacy_offset > 0 else "no_resume",
        }
    # A restart at offset 0 is a fresh scan: whatever sighting keys an older
    # page-cap run counted must not suppress this run's counts.
    scan_start_offset = int(resume_info["start_offset"])
    is_resume = scan_start_offset > 0
    seen_sighting_keys = (
        set(resume_info.get("sighting_keys") or []) if is_resume else set()
    )
    try:
        offset = max(0, int(scan_start_offset or 0))
    except (TypeError, ValueError):
        offset = 0
    start_offset = offset
    sess = requests.Session()
    sess.trust_env = False
    hit_page_cap = False
    last_can_continue = False
    scanned_oldest: int | None = None
    sighting_keys: set[str] = set(seen_sighting_keys) if is_resume else set()

    page_limit = max(1, int(max_pages))
    t0 = time.time()
    stop_reason = "completed"
    # Earliest publish_ts seen by an *earlier* resumed run; kept so the returned
    # resume state never looks younger than the chain actually scanned. A
    # restart at page 0 deliberately forgets it.
    prior_oldest = resume_info.get("resume_oldest_ts") if is_resume else None

    def _merge_and_count() -> tuple[list[dict[str, Any]], int, set[str]]:
        keys: set[str] = set()
        merged = merge_articles_with_sightings(
            articles,
            sightings or [],
            cutoff_ts=cutoff,
            biz=biz,
            collect_sighting_keys=keys,
        )
        merged_sightings = _count_new_sightings(
            merged,
            articles,
            seen_sighting_keys=seen_sighting_keys if is_resume else None,
        )
        return merged, merged_sightings, keys | sighting_keys

    def _merged_oldest() -> int | None:
        oldest = scanned_oldest
        try:
            po = int(prior_oldest) if prior_oldest is not None else 0
        except (TypeError, ValueError):
            po = 0
        if po > 0:
            oldest = po if oldest is None else min(oldest, po)
        return oldest

    def _resume_fields(
        merged_len: int, keys: set[str], *, can_continue: bool
    ) -> dict[str, Any]:
        """Payload the *next* click needs; self-contained (no UI state required)."""
        oldest = _merged_oldest()
        return {
            # ``start_offset`` / ``next_offset`` = where the next run begins.
            "start_offset": offset,
            "next_offset": offset if can_continue else None,
            # Where *this* run began (0 = fresh scan, >0 = continued).
            "scanned_from_offset": start_offset,
            "oldest_ts": oldest,
            "scanned_oldest_ts": oldest,
            "biz": biz,
            "__biz": biz,
            "merged_articles": merged_len,
            "article_count": merged_len,
            "sighting_keys": sorted(keys),
            "can_continue": bool(can_continue),
            "pages": pages,
        }

    for i in range(page_limit):
        if should_cancel and should_cancel():
            partial, partial_sightings, keys = _merge_and_count()
            resume_state = _resume_fields(
                len(partial), keys, can_continue=False
            )
            return {
                "ok": False,
                "cancelled": True,
                "error": "已取消",
                "articles": partial,
                "pages": pages,
                "days": days,
                "cutoff_ts": cutoff,
                "warning": "",
                "merged_sightings": partial_sightings,
                "stopped_reason": "cancelled",
                "__biz": biz,
                "start_offset": start_offset,
                "next_offset": None,
                "can_continue": False,
                "scanned_oldest_ts": resume_state["oldest_ts"],
                "resume": resume_state,
            }
        if on_progress:
            elapsed = int(time.time() - t0)
            prefix = "续翻" if start_offset else "正在拉取"
            on_progress(
                f"{prefix}第 {i + 1} 页（已收录 {len(articles)} 篇 · 已用 {elapsed}s）…"
            )
        try:
            page = fetch_getmsg_page(
                cred, offset=offset, count=count, session=sess, timeout=timeout
            )
        except Exception as exc:  # noqa: BLE001 - never swallow into 未知错误
            page = {
                "ok": False,
                "error": describe_exception(exc),
                "articles": [],
                "can_continue": False,
                "next_offset": None,
            }
        pages += 1
        if not page.get("ok"):
            partial, partial_sightings, keys = _merge_and_count()
            err_text = page.get("error") or "getmsg 失败"
            hint = _rate_limit_hint(err_text)
            stopped = classify_stopped_reason(err_text)
            if hint:
                err_text += hint
            resume_state = _resume_fields(len(partial), keys, can_continue=False)
            return {
                "ok": False,
                "error": err_text,
                "articles": partial,
                "pages": pages,
                "days": days,
                "cutoff_ts": cutoff,
                "warning": "",
                "merged_sightings": partial_sightings,
                "stopped_reason": stopped,
                "__biz": biz,
                "start_offset": start_offset,
                "next_offset": None,
                "can_continue": False,
                "scanned_oldest_ts": resume_state["oldest_ts"],
                "resume": resume_state,
            }

        batch = page.get("articles") or []
        last_can_continue = bool(page.get("can_continue"))
        raw_msg_count = 0
        try:
            raw = page.get("raw") or {}
            gml = raw.get("general_msg_list") or ""
            if isinstance(gml, str):
                gml_obj = json.loads(gml) if gml.strip() else {}
            else:
                gml_obj = gml if isinstance(gml, dict) else {}
            raw_msg_count = len(gml_obj.get("list") or [])
        except Exception:
            raw_msg_count = len(batch)

        _page_ts = [int(a.get("publish_ts") or 0) for a in batch]
        _debug_log(
            "PAGE "
            + json.dumps(
                {
                    "page": i + 1,
                    "offset": offset,
                    "next_offset": page.get("next_offset"),
                    "can_continue": page.get("can_continue"),
                    "raw_msg_count": raw_msg_count,
                    "parsed": len(batch),
                    "ts_min": min(_page_ts) if _page_ts else None,
                    "ts_max": max(_page_ts) if _page_ts else None,
                    "titles": [str(a.get("title") or "") for a in batch],
                },
                ensure_ascii=False,
            )
        )

        for a in batch:
            ts = int(a.get("publish_ts") or 0)
            if ts:
                scanned_oldest = ts if scanned_oldest is None else min(scanned_oldest, ts)
            if cutoff is not None and ts and ts < cutoff:
                continue
            if upper_ts is not None and ts and ts > upper_ts:
                continue
            row = dict(a)
            row.setdefault("source", "getmsg")
            articles.append(row)

        newest = _page_newest_ts(batch)
        page_fully_old = bool(batch) and newest > 0 and cutoff is not None and newest < cutoff

        if page_fully_old:
            stop_reason = "page_fully_old"
            break
        if raw_msg_count <= 0 and not batch:
            stop_reason = "empty_page"
            break

        nxt = page.get("next_offset")
        if nxt is None:
            nxt_i = offset + int(count)
        else:
            try:
                nxt_i = int(nxt)
            except Exception:
                stop_reason = "bad_next_offset"
                break
        if nxt_i <= offset:
            stop_reason = "next_offset_not_advancing"
            break

        offset = nxt_i

        if i + 1 < page_limit:
            delay = sleep_s
            if sleep_jitter > 0:
                delay *= random.uniform(max(0.0, 1 - sleep_jitter), 1 + sleep_jitter)
            if cooldown_every > 0 and (i + 1) % cooldown_every == 0:
                delay += max(0.0, cooldown_extra_s)
            time.sleep(delay)
        else:
            hit_page_cap = True

    _debug_log(
        "STOP reason=" + stop_reason + " pages=" + str(pages) + " raw_articles=" + str(len(articles))
        + " cutoff=" + str(cutoff) + " upper=" + str(upper_ts)
    )
    deduped, merged_extra, keys = _merge_and_count()
    _debug_log(
        "RESULT deduped=" + str(len(deduped)) + " raw=" + str(len(articles))
        + " merged_sightings=" + str(len(sightings or []))
    )
    if upper_ts is not None:
        deduped = [a for a in deduped if not (int(a.get("publish_ts") or 0) > upper_ts)]

    can_continue = bool(hit_page_cap and last_can_continue)
    # Legacy field kept as-is (offset reached when the page cap stopped us).
    next_offset = offset if hit_page_cap else None
    resume_state = _resume_fields(len(deduped), keys, can_continue=can_continue)

    warn = ""
    if hit_page_cap and last_can_continue:
        warn = (
            f"已达翻页上限 {page_limit} 页，{scope}可能仍有文章未拉完；"
            f"再点一次拉取将从 offset={offset} 接着上次进度续翻。"
        )
    if merged_extra:
        extra = f"已合并补录/抓包 {merged_extra} 篇"
        warn = f"{warn} · {extra}" if warn else extra

    if on_progress:
        msg = f"完成：{scope}共 {len(deduped)} 篇（请求 {pages} 页）"
        if warn:
            msg += f" · {warn}"
        on_progress(msg)

    return {
        "ok": True,
        "error": "",
        "warning": warn,
        "articles": deduped,
        "pages": pages,
        "days": days,
        "cutoff_ts": cutoff,
        "start_ts": cutoff if range_mode else None,
        "end_ts": upper_ts if range_mode else None,
        "hit_page_cap": hit_page_cap,
        "merged_sightings": merged_extra,
        "__biz": biz,
        "stopped_reason": "completed",
        "start_offset": start_offset,
        "next_offset": next_offset,
        "can_continue": can_continue,
        "scanned_oldest_ts": resume_state["oldest_ts"],
        "merged_articles": len(deduped),
        "article_count": len(deduped),
        # Additive: actual paging stop reason (``stopped_reason`` stays "completed"
        # for backward compatibility, as before).
        "paging_stop_reason": stop_reason,
        "resume": resume_state,
        "resume_restarted": bool(resume_info.get("restarted")),
        "resume_reason": str(resume_info.get("reason") or ""),
    }
