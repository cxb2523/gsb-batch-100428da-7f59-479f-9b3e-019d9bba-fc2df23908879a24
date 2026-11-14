"""合成阶段：按稳定标识去重（留新），按新鲜度+来源权重排序，截断上限。"""

from __future__ import annotations

from typing import List

from .model import Entry, SourceOutcome


def dedupe(entries: List[Entry]) -> List[Entry]:
    """按条目 id 去重，保留 published 较新的一条。

    发布时间相同则保留来源权重较高的一条；仍相同则保留先出现的一条
    （输入顺序已由采集阶段按源名排序固定，故结果确定）。
    """
    best: dict[str, Entry] = {}
    for entry in entries:
        current = best.get(entry.id)
        if current is None:
            best[entry.id] = entry
        elif (entry.published, entry.weight) > (current.published, current.weight):
            best[entry.id] = entry
    return list(best.values())


def rank(entries: List[Entry], max_items: int) -> List[Entry]:
    """新鲜度优先、来源权重次之；id 作为最终 tie-break 保证确定性。"""
    ordered = sorted(
        entries,
        key=lambda e: (-e.published.timestamp(), -e.weight, e.id),
    )
    return ordered[:max_items]


def synthesize(outcomes: List[SourceOutcome], max_items: int) -> List[Entry]:
    """汇总各源条目 -> 去重 -> 排序 -> 截断。"""
    pool: List[Entry] = []
    for outcome in outcomes:  # outcomes 已按源名排序
        pool.extend(outcome.entries)
    return rank(dedupe(pool), max_items)
