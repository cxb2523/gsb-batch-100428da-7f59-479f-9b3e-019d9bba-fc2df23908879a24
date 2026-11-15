#!/usr/bin/env python3
"""
公众号每天收集推文 —— 主程序

每天定时跑一次：把目标公众号"新出现"的文章存进素材库，自动去重。
支持两种采集源（config.yaml 里 source 切换）：
  - rss : 拉取 RSS 地址（配合 Wechat2RSS 等）
  - mp  : 用你登录的公众号后台接口，按名字搜号并拉文章（全覆盖，需登录态）

用法：
    python3 collect.py                # 用 config.yaml
    python3 collect.py -c other.yaml  # 指定配置
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

import yaml

from storage import Article, get_storage

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "seen.json"
MP_ACCOUNTS_FILE = ROOT / "state" / "mp_accounts.json"   # 缓存号名→fakeid
MP_CHECKPOINT_FILE = ROOT / "state" / "mp_checkpoint.json"  # mp 逐号结局+断点（独立于 seen）
MP_SESSION_FILE = ROOT / "mp_session.json"               # token + cookie
_CST = dt.timezone(dt.timedelta(hours=8))                # 北京时间

# mp 采集的逐号结局
MP_DONE = "done"                       # 完成（本次配额页全部跑完 / 翻到空页）
MP_SESSION_EXPIRED = "session_expired"  # 登录态失效（中断，留断点）
MP_RATE_LIMITED = "rate_limited"       # 被限频（中断，留断点）
MP_SEARCH_FAILED = "search_failed"     # 搜号失败（没搜到 / 搜号请求异常）
MP_FETCH_ERROR = "fetch_error"         # 拉取异常（文章列表请求非登录态/限频类错误）
MP_OUTCOME_LABELS = {
    MP_DONE: "完成",
    MP_SESSION_EXPIRED: "登录态失效",
    MP_RATE_LIMITED: "被限频",
    MP_SEARCH_FAILED: "搜号失败",
    MP_FETCH_ERROR: "拉取异常",
}
# 属于「中断」、下次需要按断点续跑的结局
MP_INTERRUPTED = (MP_SESSION_EXPIRED, MP_RATE_LIMITED)
# 「存在中断/失败」的退出码（0=全部完成），供 launchd/cron 判断
EXIT_MP_INCOMPLETE = 3


def log(msg: str) -> None:
    ts = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ----------------------------- 通用：状态读写 -----------------------------
def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def epoch_to_cst(ts) -> str:
    try:
        return dt.datetime.fromtimestamp(int(ts), _CST).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def guid_of(account: str, key: str) -> str:
    return hashlib.sha1(f"{account}::{key}".encode("utf-8")).hexdigest()


# ----------------------------- 采集源 1：RSS -----------------------------
def collect_rss(cfg, storage, seen) -> tuple[int, int]:
    import feedparser

    def resolve_url(url: str) -> str:
        return str((ROOT / url).resolve()) if "://" not in url else url

    save_full = bool(cfg.get("save_full_content", True))
    feeds = cfg.get("feeds") or []
    new_total = err_total = 0

    for feed in feeds:
        name, url = feed.get("name", "未命名"), feed.get("url", "")
        if not url:
            log(f"⚠️  [{name}] 没有 url，跳过")
            continue
        log(f"拉取 [{name}] …")
        try:
            parsed = feedparser.parse(resolve_url(url))
        except Exception as e:
            log(f"❌ [{name}] 拉取异常：{e}")
            err_total += 1
            continue
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            log(f"❌ [{name}] 解析失败/空源")
            err_total += 1
            continue

        new_here = 0
        for entry in parsed.entries:
            key = getattr(entry, "id", "") or getattr(entry, "link", "") \
                or getattr(entry, "title", "")
            guid = guid_of(name, key)
            if guid in seen:
                continue
            tm = getattr(entry, "published_parsed", None) \
                or getattr(entry, "updated_parsed", None)
            published = (
                dt.datetime(*tm[:6], tzinfo=dt.timezone.utc).astimezone(_CST)
                .strftime("%Y-%m-%d %H:%M:%S")
                if tm else getattr(entry, "published", "")
            )
            content = ""
            if save_full:
                c = getattr(entry, "content", None)
                content = (c[0].get("value", "") if c else "") \
                    or getattr(entry, "summary", "")
            art = Article(
                account=name, title=getattr(entry, "title", "无标题").strip(),
                link=getattr(entry, "link", ""), published=published,
                summary=getattr(entry, "summary", ""), content=content, guid=guid,
            )
            try:
                storage.save(art)
                seen.add(guid)
                new_here += 1
                new_total += 1
            except Exception as e:
                log(f"   ❌ 存储失败《{art.title}》：{e}")
                err_total += 1
        log(f"   [{name}] 本次新增 {new_here} 篇")
        time.sleep(1)
    return new_total, err_total


# ------------------------- 采集源 2：公众号后台 mp -------------------------
def _now_cst_str() -> str:
    return dt.datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")


def load_mp_checkpoint() -> dict:
    """
    读逐号结局/断点文件。文件缺失或损坏（非合法 JSON、结构不对）一律按
    「没有断点、从头采集」处理，返回空结构，绝不抛错中断采集。
    """
    data = load_json(MP_CHECKPOINT_FILE, None)
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        return {"accounts": {}}
    return data


def record_mp_outcome(checkpoint: dict, name: str, outcome: str, *,
                      fakeid: str = "", pages_done: int = 0, new_count: int = 0,
                      begin: int = 0, detail: str = "") -> None:
    """
    登记一个号本次的结局并立即落盘（逐号留痕，中途崩溃也能核对）。
    中断类结局（登录态失效/被限频）且拿到了 fakeid 时写入断点 {fakeid, begin}，
    供下次续跑；终局（完成/搜号失败/拉取异常）或连号都没解析出来时不带断点，
    下次从第 0 页重新增量。
    """
    if outcome in MP_INTERRUPTED and fakeid:
        resume = {"fakeid": str(fakeid), "begin": int(begin)}
    else:
        resume = None
    checkpoint["accounts"][name] = {
        "outcome": outcome,
        "pages_done": int(pages_done),
        "new_count": int(new_count),
        "resume": resume,
        "detail": detail,
        "updated_at": _now_cst_str(),
    }
    save_json(MP_CHECKPOINT_FILE, checkpoint)


def collect_mp(cfg, storage, seen) -> tuple[int, int, int]:
    """
    逐号采集公众号文章。返回 (新增总数, 失败计数, 未完成号数)。
    每个号的结局写入 state/mp_checkpoint.json：
      - 登录态失效/被限频不再跳出整批，只把当前号标为中断并记断点，继续后面的号；
      - 下次运行时，中断号从断点 begin 续翻，其余号仍从第 0 页增量；
      - 已入库文章靠 seen 去重，续跑不会重复写素材库。
    """
    from mp_client import (MPClient, MPFreqError, MPSessionError,
                           fetch_article_text)

    session = load_json(MP_SESSION_FILE, {})
    token, cookie = session.get("token", ""), session.get("cookie", "")

    mp_cfg = cfg.get("mp") or {}
    accounts = mp_cfg.get("accounts") or []
    pages = int(mp_cfg.get("pages", 1))
    save_full = bool(cfg.get("save_full_content", True))
    page_size = 20

    checkpoint = load_mp_checkpoint()
    new_total = err_total = 0

    if not token or not cookie:
        log(f"❌ 缺少登录态。请把 token 和 cookie 填进 {MP_SESSION_FILE.name}"
            f"（参考 mp_session.example.json）")
        # 每个号都留一条「登录态失效」，便于核对本批哪些号没跑；不写断点，
        # 下次补上登录态后从第 0 页正常增量。
        for name in accounts:
            record_mp_outcome(checkpoint, name, MP_SESSION_EXPIRED,
                              detail="缺少 token/cookie，未发起请求")
        return 0, 1, len(accounts)

    client = MPClient(token, cookie)
    fakeid_cache = load_json(MP_ACCOUNTS_FILE, {})   # {号名: {fakeid, nickname}}

    for name in accounts:
        log(f"采集 [{name}] …")
        new_here = 0
        pages_done = 0

        # 1) 解析 fakeid（缓存优先，避免频繁搜号被限频）
        rec = fakeid_cache.get(name)
        if not rec or not rec.get("fakeid"):
            try:
                hit = client.resolve_fakeid(name)
            except MPSessionError as e:
                # 不再 break 整批：当前号留痕，继续后面的号（可能还有缓存号可跑）
                log(f"❌ {e}　→ 登录态失效，[{name}] 标记中断，继续后续账号。")
                notify_session_expired(cfg, str(e))
                record_mp_outcome(checkpoint, name, MP_SESSION_EXPIRED,
                                  detail=str(e))
                continue
            except MPFreqError as e:
                log(f"⏳ {e}　→ 搜号被限频，[{name}] 标记中断，继续后续账号。")
                record_mp_outcome(checkpoint, name, MP_RATE_LIMITED, detail=str(e))
                continue
            except Exception as e:
                log(f"   ❌ [{name}] 搜号失败：{e}")
                err_total += 1
                record_mp_outcome(checkpoint, name, MP_SEARCH_FAILED, detail=str(e))
                continue
            if not hit:
                log(f"   ⚠️ [{name}] 没搜到这个号，检查名字是否准确")
                err_total += 1
                record_mp_outcome(checkpoint, name, MP_SEARCH_FAILED,
                                  detail="searchbiz 未返回候选")
                continue
            rec = {"fakeid": hit["fakeid"], "nickname": hit.get("nickname", name)}
            fakeid_cache[name] = rec
            save_json(MP_ACCOUNTS_FILE, fakeid_cache)
            log(f"   ✓ 命中「{rec['nickname']}」(fakeid={rec['fakeid']})")
            time.sleep(2)

        # 2) 决定起始页：只有上次中断（失效/限频）且 fakeid 对得上的号才续跑，
        #    其余情况（完成/搜号失败/拉取异常/断点损坏）从第 0 页重新增量。
        saved = checkpoint["accounts"].get(name) or {}
        resume = saved.get("resume")
        if (isinstance(resume, dict)
                and saved.get("outcome") in MP_INTERRUPTED
                and str(resume.get("fakeid", "")) == str(rec["fakeid"])):
            start_begin = int(resume.get("begin") or 0)
            if start_begin > 0:
                log(f"   ↪ 上次中断于 begin={start_begin}，从该页继续翻页")
        else:
            start_begin = 0

        # 3) 拉文章列表（本次仍只跑 pages 页配额；已入库的靠 seen 去重）
        try:
            for pg in range(pages):
                begin = start_begin + pg * page_size
                arts = client.list_articles(rec["fakeid"], begin=begin,
                                            count=page_size)
                if not arts:
                    break
                pages_done += 1
                for a in arts:
                    link = a.get("link", "")
                    guid = guid_of(name, link or a.get("title", ""))
                    if guid in seen:
                        continue
                    published = epoch_to_cst(a.get("update_time")
                                             or a.get("create_time"))
                    content = ""
                    if save_full and link:
                        content = fetch_article_text(link)
                        time.sleep(1)
                    art = Article(
                        account=name, title=(a.get("title") or "无标题").strip(),
                        link=link, published=published,
                        summary=a.get("digest", ""), content=content, guid=guid,
                        extra={"author": a.get("author_name", "")},
                    )
                    try:
                        storage.save(art)
                        seen.add(guid)
                        new_here += 1
                        new_total += 1
                    except Exception as e:
                        log(f"   ❌ 存储失败《{art.title}》：{e}")
                        err_total += 1
                time.sleep(2)
        except MPSessionError as e:
            # 当前页没拉成：断点指向下次要重试的这一页（begin），已处理页数不增
            log(f"❌ {e}　→ 登录态失效，[{name}] 中断于 begin={begin}，"
                f"已处理 {pages_done} 页；继续后续账号。")
            notify_session_expired(cfg, str(e))
            record_mp_outcome(checkpoint, name, MP_SESSION_EXPIRED,
                              fakeid=rec["fakeid"], pages_done=pages_done,
                              new_count=new_here, begin=begin, detail=str(e))
            time.sleep(3)
            continue
        except MPFreqError as e:
            log(f"⏳ {e}　→ 被限频，[{name}] 中断于 begin={begin}，"
                f"已处理 {pages_done} 页；继续后续账号。")
            record_mp_outcome(checkpoint, name, MP_RATE_LIMITED,
                              fakeid=rec["fakeid"], pages_done=pages_done,
                              new_count=new_here, begin=begin, detail=str(e))
            time.sleep(3)
            continue
        except Exception as e:
            log(f"   ❌ [{name}] 拉文章失败：{e}")
            err_total += 1
            record_mp_outcome(checkpoint, name, MP_FETCH_ERROR,
                              fakeid=rec["fakeid"], pages_done=pages_done,
                              new_count=new_here, detail=str(e))
            time.sleep(3)
            continue

        log(f"   [{name}] 本次新增 {new_here} 篇")
        record_mp_outcome(checkpoint, name, MP_DONE, fakeid=rec["fakeid"],
                          pages_done=pages_done, new_count=new_here)
        time.sleep(3)   # 号与号之间慢一点，降低限频风险

    interrupted = log_mp_summary(checkpoint, accounts)
    return new_total, err_total, interrupted


def log_mp_summary(checkpoint: dict, accounts: list[str]) -> int:
    """按号输出本次结局汇总。返回未完成（中断/失败）的号数。"""
    log("— mp 逐号结局汇总 —")
    incomplete = 0
    for name in accounts:
        rec = checkpoint["accounts"].get(name)
        if not rec:
            log(f"  · [{name}] 无结局记录")
            incomplete += 1
            continue
        label = MP_OUTCOME_LABELS.get(rec.get("outcome"), rec.get("outcome", "?"))
        resume = rec.get("resume")
        tip = ""
        if resume:
            tip = f"，断点 fakeid={resume.get('fakeid', '')} begin={resume.get('begin', 0)}"
        log(f"  · [{name}] {label}｜已处理 {rec.get('pages_done', 0)} 页"
            f"｜本次新增 {rec.get('new_count', 0)} 篇{tip}")
        if rec.get("outcome") != MP_DONE:
            incomplete += 1
    return incomplete


# ----------------------------- 登录态探针 + 推送 -----------------------------
def notify_session_expired(cfg, reason: str = "") -> None:
    """登录态失效时推一条到手机（Bark）。静默失败，绝不影响采集。"""
    try:
        from notify import push_bark
    except Exception:
        return
    body = "mp_session.json 的 token/cookie 失效了，去公众号后台重新扫码、更新一次。"
    if reason:
        body += f"（{reason}）"
    sent = push_bark(
        cfg, "🔑 公众号采集·登录态过期", body,
        click_url="https://mp.weixin.qq.com",
        event="session_expired", min_interval_sec=12 * 3600,   # 12h 内只提醒一次
    )
    log("📲 已推送失效提醒到手机" if sent else "（未推送：通知未启用/未填 key/被节流）")


def notify_new_articles(cfg, new_total: int, pending: int) -> None:
    """采集到新推文时推一条到手机（Bark），提醒去 ima 批量导入。静默失败。"""
    if new_total <= 0:
        return
    try:
        from notify import push_bark
    except Exception:
        return
    body = (f"今天新增 {new_total} 篇推文，待导入 ima 共 {pending} 条。"
            f"去电脑打开「链接清单-待导入.txt」按批粘进 ima。")
    sent = push_bark(
        cfg, "📰 公众号采集·有新推文", body,
        event="new_articles", min_interval_sec=0,   # 有新就提醒，不节流
    )
    log("📲 已推送新推文提醒到手机" if sent else "（未推送：通知未启用/未填 key）")


def probe_mp(cfg) -> int:
    """
    只检查 mp 登录态是否有效，不采集、不写库。失效则推送提醒。
    退出码：0 有效（或仅被限频）/ 1 没填登录态 / 2 请求异常 / 3 登录态失效。
    """
    from mp_client import MPClient, MPFreqError, MPSessionError

    session = load_json(MP_SESSION_FILE, {})
    token, cookie = session.get("token", ""), session.get("cookie", "")
    if not token or not cookie:
        log(f"❌ {MP_SESSION_FILE.name} 里没有 token/cookie")
        notify_session_expired(cfg, "mp_session.json 为空")
        return 1

    accounts = (cfg.get("mp") or {}).get("accounts") or []
    probe_kw = accounts[0] if accounts else "微信"   # 用已配置的号名做最小搜号探测
    try:
        MPClient(token, cookie).search_account(probe_kw)
        log("✅ 登录态有效")
        try:
            from notify import clear_event
            clear_event("session_expired")   # 恢复正常，下次失效能立即提醒
        except Exception:
            pass
        return 0
    except MPFreqError as e:
        log(f"⏳ 被限频，但登录态本身有效：{e}")
        return 0
    except MPSessionError as e:
        log(f"❌ 登录态已失效：{e}")
        notify_session_expired(cfg, str(e))
        return 3
    except Exception as e:
        log(f"⚠️ 探针请求异常（不一定是登录态问题）：{e}")
        return 2


# --------------------------------- 主流程 ---------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="公众号推文每日收集")
    ap.add_argument("-c", "--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--probe", action="store_true",
                    help="只检查 mp 登录态是否有效，失效则推送提醒后退出（不采集）")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    source = (cfg.get("source") or "rss").lower()

    if args.probe:
        return probe_mp(cfg)

    try:
        storage = get_storage(cfg.get("storage", "local"), cfg)
    except Exception as e:
        log(f"❌ 初始化存储失败：{e}")
        return 2

    seen = set(load_json(STATE_FILE, []))
    log(f"采集源：{source}　存储：{cfg.get('storage', 'local')}")

    if source == "rss":
        new_total, err_total = collect_rss(cfg, storage, seen)
        incomplete = 0
    elif source == "mp":
        new_total, err_total, incomplete = collect_mp(cfg, storage, seen)
    else:
        log(f"❌ 未知 source：{source}（可选 rss / mp）")
        return 1

    storage.close()
    save_json(STATE_FILE, sorted(seen))
    log(f"完成 ✅ 共新增 {new_total} 篇，失败 {err_total} 处。已记录去重状态。")

    # 重建链接清单（仅本地 Markdown 存储时有意义）。失败不影响采集本身。
    pending = 0
    if (cfg.get("storage", "local") or "local").lower() == "local":
        try:
            from export_links import export
            out_dir = (cfg.get("local") or {}).get("output_dir", "素材库")
            res = export(ROOT / out_dir)
            pending = res["pending"]
            log(f"链接清单已更新 ✅ 共 {res['total']} 条（滤掉非营销 {res['filtered']} 条），"
                f"待导入 ima {pending} 条 → 链接清单.md / .csv / -待导入.txt")
        except Exception as e:
            log(f"⚠️ 链接清单生成失败（不影响采集）：{e}")

    # 有新推文就推手机提醒去 ima 导入（待导入数取自上面的链接清单）
    notify_new_articles(cfg, new_total, pending)

    # mp 采集时：只要有号中断（登录态失效/限频）或失败，就用非 0 退出码区分，
    # 方便 launchd/cron 判断本次是否需要关注（rss 路径行为不变，始终 0）。
    if source == "mp" and incomplete:
        log(f"⚠️ 本次有 {incomplete} 个号未完成（见上方逐号结局汇总，"
            f"断点已写入 {MP_CHECKPOINT_FILE.name}，下次自动续跑）。退出码 {EXIT_MP_INCOMPLETE}。")
        return EXIT_MP_INCOMPLETE
    return 0


if __name__ == "__main__":
    sys.exit(main())
