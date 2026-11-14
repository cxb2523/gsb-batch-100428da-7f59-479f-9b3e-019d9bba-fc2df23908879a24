"""bili hot: what's trending — hot search, popular, weekly picks, all-time classics, rankings."""

from __future__ import annotations

from typing import Any, Optional

import typer
from bilibili_api import hot, rank, search
from bilibili_api.utils.aid_bvid_transformer import aid2bvid
from bilibili_api.utils.network import Api

from .. import config, output
from ..cards import filter_unseen, load_seen, video_card
from ..errors import BiliError, from_exception
from ..runtime import run

app = typer.Typer(help="热门：热搜词、综合热门、每周必看、入站必刷、排行榜。", no_args_is_help=True)

UNSEEN_OPT = typer.Option(False, "--unseen", help="过滤掉已经用 `bili video watch` 看过的视频。")
HOT_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/square"

RANK_ZONES = {
    "all": rank.RankType.All, "anime": rank.RankType.Douga, "music": rank.RankType.Music,
    "dance": rank.RankType.Dance, "game": rank.RankType.Game, "knowledge": rank.RankType.Knowledge,
    "tech": rank.RankType.Technology, "sports": rank.RankType.Sports, "car": rank.RankType.Car,
    "life": rank.RankType.Life, "food": rank.RankType.Food, "animal": rank.RankType.Animal,
    "kichiku": rank.RankType.Kichiku, "fashion": rank.RankType.Fashion, "ent": rank.RankType.Ent,
    "film": rank.RankType.Cinephile, "original": rank.RankType.Original, "rookie": rank.RankType.Rookie,
}


@app.command("search")
def search_(limit: int = typer.Option(10, "--limit", min=1, max=50, help="返回条数。")) -> None:
    """B 站热搜榜（搜索框下的“bilibili热搜”）。"""

    async def body():
        try:
            data = await Api(url=HOT_SEARCH_URL, method="GET", wbi=True, credential=config.optional_credential()).update_params(limit=limit).result
            items = (data.get("trending") or {}).get("list") or []
            source = "search/square"
        except Exception:  # noqa: BLE001 - older endpoint as fallback
            data = await search.get_hot_search_keywords()
            items, source = data.get("list") or [], "main/hotword"
        if output.settings.raw:
            return data
        return {
            "source": source,
            "items": [
                {"rank": i + 1, "keyword": it.get("keyword"), "title": it.get("show_name") or it.get("keyword"), "heat": it.get("heat_score")}
                for i, it in enumerate(items[:limit])
            ],
            "next": "用 `bili search video <关键词>` 查看相关视频。",
        }

    run(body)


@app.command()
def popular(
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(20, "--size", min=1, max=50, help="每页条数。"),
    unseen: bool = UNSEEN_OPT,
) -> None:
    """综合热门：当前全站热门视频，附推荐理由（如“百万播放”）。"""

    async def body():
        data = await hot.get_hot_videos(pn=page, ps=size)
        if output.settings.raw:
            return data
        return {"page": page, "items": filter_unseen([video_card(v) for v in data.get("list") or []], unseen), "no_more": data.get("no_more")}

    run(body)


@app.command()
def weekly(
    issue: Optional[int] = typer.Option(None, "--issue", help="期数，默认最新一期。"),
    list_issues: bool = typer.Option(False, "--list", help="只列出最近的期数和主题。"),
    unseen: bool = UNSEEN_OPT,
) -> None:
    """每周必看：B 站官方每周精选。"""

    async def body():
        issues = (await hot.get_weekly_hot_videos_list()).get("list") or []
        if list_issues:
            return {"issues": [{"issue": i.get("number"), "name": i.get("name"), "subject": i.get("subject")} for i in issues[:20]]}
        number = issue or (issues[0]["number"] if issues else None)
        if number is None:
            raise BiliError("not_found", "没有找到每周必看。")
        data = await hot.get_weekly_hot_videos(number)
        if output.settings.raw:
            return data
        conf = data.get("config") or {}
        return {"issue": number, "name": conf.get("name"), "subject": conf.get("subject"), "items": filter_unseen([video_card(v) for v in data.get("list") or []], unseen)}

    run(body)


@app.command()
def precious(unseen: bool = UNSEEN_OPT) -> None:
    """入站必刷：B 站官方收录的经典视频。"""

    async def body():
        data = await hot.get_history_popular_videos()
        if output.settings.raw:
            return data
        return {"items": filter_unseen([video_card(v) for v in data.get("list") or []], unseen)}

    run(body)


RANK_PAGE_SIZE = 30  # 排行榜每页条数（也是网页榜一屏的条数）
RANK_URL = "https://api.bilibili.com/x/v2/rank/region"
STOP_LIMIT = "limit"      # 过滤后已够 --limit 条
STOP_PAGES = "pages"      # --pages 的页数用完了
STOP_NO_MORE = "no_more"  # 接口表示没有更多数据
STOP_ERROR = "error"      # 某页请求失败


def _app_video(item: dict[str, Any]) -> dict[str, Any] | None:
    """App 端排行榜的一条（aid 在 param 里）→ 网页版同形的字段，让 video_card 通用。"""
    aid = item.get("param")
    if not str(aid or "").isdigit():
        return None
    return {
        "bvid": aid2bvid(int(aid)),
        "title": item.get("title"),
        "owner": {"mid": item.get("mid"), "name": item.get("name")},
        "duration": item.get("duration"),
        "pubdate": item.get("pub_date"),
        "stat": {"view": item.get("play"), "like": item.get("like"), "danmaku": item.get("danmaku"), "reply": item.get("reply")},
    }


async def _rank_page(zone: str, page: int) -> dict[str, Any]:
    """排行榜第 `page` 页（从 1 开始）。

    网页的 ranking/v2（bilibili-api 的 rank.get_rank）一次返回整份榜单，不接受页码参数；
    要分页只能走 App 的 /x/v2/rank/region（pn/ps）。全站榜和大多数分区与网页榜逐条一致
    （运动、生活两个分区的中后段顺序略有出入）。原创、新人榜没有分页接口，网页接口一次
    给全，所以它们只有第 1 页，第 2 页起接口会直接说没有更多。

    返回 {"items": 网页同形的条目, "raw": 该页原始条数, "payload": 该页原始数据,
          "paged": 是否还有下一页, "no_more": 是否已经翻到头}。
    """
    info = RANK_ZONES[zone].value
    if info.get("type") != "all":  # 原创 / 新人：整份榜单一次给完
        data = await rank.get_rank(RANK_ZONES[zone])
        items = list((data.get("list") if isinstance(data, dict) else data) or [])
        return {"items": items, "raw": len(items), "payload": data, "paged": False, "no_more": True}

    payload = await Api(url=RANK_URL, method="GET").update_params(rid=info.get("rid", 0), pn=page, ps=RANK_PAGE_SIZE).result
    raw_items = payload if isinstance(payload, list) else (payload.get("list") if isinstance(payload, dict) else None)
    raw_items = [x for x in raw_items or [] if isinstance(x, dict)]
    if isinstance(payload, dict):
        if "has_more" in payload:
            no_more = not payload["has_more"]
        elif "no_more" in payload:
            no_more = bool(payload["no_more"])
        else:  # 没有显式字段：空页或装不满一页都是翻到头了
            no_more = len(raw_items) < RANK_PAGE_SIZE
    else:
        no_more = len(raw_items) < RANK_PAGE_SIZE
    items = [card for card in (_app_video(x) for x in raw_items) if card]
    return {"items": items, "raw": len(raw_items), "payload": payload, "paged": True, "no_more": no_more}


async def collect_rank(
    zone: str, pages: int, limit: int, unseen: bool, fetch=None
) -> tuple[dict[str, Any], list[Any]]:
    """按页串行采集排行榜，返回（给 agent 的结果, 每页的 B 站原始数据）。

    - `pages` 是最多向后取几页，每页 RANK_PAGE_SIZE 条。
    - 每页先按 `--unseen` 剔除看过的，再累加；过滤后的条数够 `--limit` 就停止翻页。
    - 结束原因见 STOP_*：够数 / 页数用完 / 接口没有更多 / 某页失败。
    - 某页失败时保留前面已采到的条目，并在 `error` 里标出页号和错误码；一页都没采到才整体报错。
    - 每页都按顺序请求，上一页返回后才请求下一页，不会并发打接口。
    """
    seen = load_seen() if unseen else {}
    fetched = fetch or _rank_page
    items: list[dict[str, Any]] = []
    page_stats: list[dict[str, Any]] = []
    raw_pages: list[Any] = []
    offset = 0          # 之前各页原始条数之和：用来给条目编真实的榜单名次
    attempts = 0        # 实际请求了几页（含失败的那一页）
    stop_reason = STOP_PAGES
    failure: BiliError | None = None

    for page in range(1, pages + 1):
        attempts = page
        try:
            data = await fetched(zone, page)
        except Exception as exc:  # noqa: BLE001 - 一页失败不能丢掉前面已经采到的结果
            failure = from_exception(exc)
            stop_reason = STOP_ERROR
            break
        raw_pages.append(data.get("payload"))

        raw = data.get("items") or []
        kept, skipped = [], 0
        for i, item in enumerate(raw):
            card = {"rank": offset + i + 1, **video_card(item)}
            if unseen and card.get("bvid") in seen:
                skipped += 1
                continue
            kept.append(card)
        page_stats.append({"page": page, "raw": data.get("raw", len(raw)), "skipped": skipped, "kept": len(kept)})
        items += kept
        offset += len(raw)

        if len(items) >= limit:
            stop_reason = STOP_LIMIT
            break
        if not data.get("paged", True) or data.get("no_more"):
            stop_reason = STOP_NO_MORE
            break
        stop_reason = STOP_PAGES

    if failure is not None and not items:
        # 一条都没采到，就没什么可保留的：按正常失败路径报错，并说明是哪一页。
        detail = {**failure.detail, "page": attempts}
        raise BiliError(failure.code, failure.message, hint=failure.hint, detail=detail)

    result: dict[str, Any] = {
        "zone": zone,
        "items": items[:limit],
        "pages_fetched": attempts,
        "pages_limit": pages,
        "page_stats": page_stats,
        "stop_reason": stop_reason,
    }
    if failure is not None:
        error: dict[str, Any] = {"page": attempts, "code": failure.code, "message": failure.message}
        if failure.detail.get("bili_code") is not None:
            error["bili_code"] = failure.detail["bili_code"]
        result["error"] = error
    return result, raw_pages


@app.command("rank")
def rank_(
    zone: str = typer.Option("all", "--zone", help=f"分区：{' / '.join(RANK_ZONES)}。"),
    limit: int = typer.Option(30, "--limit", min=1, max=100, help="返回条数，作用于 --unseen 过滤后的结果。"),
    pages: int = typer.Option(1, "--pages", min=1, max=10, help=f"最多向后取几页，每页 {RANK_PAGE_SIZE} 条；过滤后不够 --limit 时会继续翻页。"),
    unseen: bool = UNSEEN_OPT,
) -> None:
    """排行榜：全站或各分区最近三天的排行，可按页连续采集（--pages）。"""

    async def body():
        if zone not in RANK_ZONES:
            raise BiliError("invalid_argument", f"--zone 只能是 {' / '.join(RANK_ZONES)} 之一。")
        data, raw_pages = await collect_rank(zone, pages, limit, unseen)
        if output.settings.raw:
            if "error" in data:
                err = data["error"]
                output.info(f"警告：第 {err['page']} 页请求失败（{err['code']}：{err['message']}），只返回了前面几页的原始数据。")
            # --raw 不套采集报告：只请求一页时就是 B 站那一页的原始返回（和以前一致），
            # 请求多页时是每页原始返回组成的列表（哪怕因为错误或没有更多只拿到一页）。
            return raw_pages[0] if pages == 1 else raw_pages
        return data

    run(body)
