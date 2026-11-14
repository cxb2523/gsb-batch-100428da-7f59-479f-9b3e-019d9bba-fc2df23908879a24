# -*- coding: utf-8 -*-
"""采集主循环：关键词 x 页 -> 快照入库，过程计数汇总给 crawl_runs。

风控熔断语义：命中滑块（CaptchaError）时，当前词已采到的搜索结果连同已补到的
想要数先正常入库，再让熔断信号向上传递，整轮按退出码 2 收尾；已完成的词不丢。
"""
import random
import time
from dataclasses import dataclass, field

import config
import db
from fetcher import BaseFetcher, CaptchaError


@dataclass
class DetailBackfillStat:
    """单个关键词的详情补数（想要数）结果。"""
    attempted: int = 0     # 实际调用详情接口的条数（含失败/熔断那一次）
    success: int = 0       # 成功拿到想要数的条数
    failed: int = 0        # 单条失败（返回 None 跳过）的条数
    saved: int = 0         # 该词本次入库的快照条数（熔断词也照实记）


@dataclass
class CrawlSummary:
    keywords_total: int = 0
    keywords_ok: int = 0
    keywords_failed: int = 0
    items_saved: int = 0
    errors: list = field(default_factory=list)   # 每条 "关键词: 错误"
    captcha: bool = False                        # True = 触发滑块熔断
    captcha_keyword: str | None = None           # 熔断发生在哪个词
    captcha_detail_step: int | None = None       # 详情补数进行到第几条（1 起；搜索阶段熔断为 None）
    first_raw: str | None = None                 # 第一次成功响应原文（供 sample 落盘）
    # 按词的详情补数统计：{关键词: DetailBackfillStat}，只含有补数动作（有搜索结果）的词
    detail_stats: dict = field(default_factory=dict)


def crawl_all(conn, fetcher: BaseFetcher, keywords: dict[str, int], run_date: str) -> CrawlSummary:
    """跑完全部关键词。返回汇总。

    命中滑块立即熔断：当前词的搜索结果与已补想要数先入库，随后停止后续词，
    summary.captcha=True 向上传递（main.py 据此走退出码 2）。
    """
    s = CrawlSummary(keywords_total=len(keywords))
    for word, kw_id in keywords.items():
        try:
            n = _crawl_keyword(conn, fetcher, word, kw_id, run_date, s)
        except CaptchaError as e:
            # 当前词已在 _crawl_keyword 内把已有记录入库；只统计已落袋的部分，不再爬后续词
            s.captcha = True
            s.captcha_keyword = word
            s.captcha_detail_step = s.detail_stats[word].attempted if word in s.detail_stats else None
            s.errors.append(f"{word}: {e}")
            if word in s.detail_stats:
                s.items_saved += s.detail_stats[word].saved
            if s.detail_stats.get(word) and s.detail_stats[word].saved:
                s.keywords_ok += 1          # 有记录入库 = 这词的数据保住了
            else:
                s.keywords_failed += 1      # 搜索阶段就熔断，一条没成
            break
        if n is None:
            s.keywords_failed += 1
        else:
            s.keywords_ok += 1
            s.items_saved += n
        time.sleep(random.uniform(config.SLEEP_MIN, config.SLEEP_MAX))   # 词间限流
    return s


def _crawl_keyword(conn, fetcher: BaseFetcher, word: str, kw_id: int,
                   run_date: str, s: CrawlSummary) -> int | None:
    """抓一个关键词的全部页。返回入库条数；整词失败返回 None（错误已记录进 s.errors）。

    详情补数命中 CaptchaError 时：先把已采记录（含已补到的想要数）入库，再把异常
    向上抛给 crawl_all 触发当日熔断——保证当前词数据不丢。搜索阶段的 CaptchaError
    同样向上抛（此时没有记录可存）。
    """
    seen, records = set(), []
    for page in range(1, config.MAX_PAGES_PER_KEYWORD + 1):
        r = fetcher.search(word, page)      # CaptchaError 直接上抛：此词还没数据
        if s.first_raw is None and r.raw:
            s.first_raw = r.raw
        if r.error == "EMPTY":
            break                                   # 翻到底，正常结束
        if not r.ok:
            s.errors.append(f"{word}: {r.error}")
            break                                   # 部分页成功也保住，整词失败下面判 records
        for it in r.items:
            if it.item_id not in seen:              # 页间去重
                seen.add(it.item_id)
                records.append(it)
        if page < config.MAX_PAGES_PER_KEYWORD:
            time.sleep(random.uniform(config.SLEEP_MIN, config.SLEEP_MAX))   # 页间限流

    if not records and s.errors and s.errors[-1].startswith(f"{word}:"):
        return None                                 # 一页都没成，不产生空补数统计

    stat = s.detail_stats.setdefault(word, DetailBackfillStat())
    try:
        _backfill_wants(fetcher, records, stat)
    except CaptchaError:
        # 熔断不丢数据：已采搜索结果 + 已补想要数先入库，再向上抛
        if records:
            stat.saved = db.replace_snapshots(conn, run_date, kw_id, records)
        raise

    stat.saved = db.replace_snapshots(conn, run_date, kw_id, records)
    return stat.saved


def _backfill_wants(fetcher: BaseFetcher, records: list,
                    stat: DetailBackfillStat) -> None:
    """抽样调详情接口补想要数（2026 搜索列表已不带 want，见 config.DETAIL_SAMPLE_SIZE）。

    单条失败（get_want 返回 None）只计数跳过，不影响同词其余记录；
    命中 CaptchaError 由调用方捕获——计数先落，异常继续上抛触发熔断。
    """
    sample = records[:config.DETAIL_SAMPLE_SIZE]
    for i, rec in enumerate(sample):
        stat.attempted += 1                         # 熔断那一次也计入尝试数
        want = fetcher.get_want(rec.item_id)       # CaptchaError 自然上抛：计数已落，上层先保数据再熔断
        if want is not None:
            rec.want_count = want
            stat.success += 1
        else:
            stat.failed += 1                     # 单条静默失败，日报里按词可见
        if i + 1 < len(sample):
            time.sleep(random.uniform(config.SLEEP_MIN, config.SLEEP_MAX))   # 详情间限流
