# -*- coding: utf-8 -*-
"""Bing/Yahoo 爬虫共享小模块：已访问 URL 持久化与运行收尾统计。

两个爬虫脚本（bing_crawler.py / yahoo_crawler.py）共用本模块，
各自使用独立的已访问记录文件与 CSV 文件，互不串写。
"""
import os

# 结束原因
REASON_COMPLETED = 'completed'      # 正常完成
REASON_INTERRUPTED = 'interrupted'  # 用户中断 (Ctrl+C)
REASON_VERIFICATION = 'verification'  # 遇到验证页面

_REASON_LABELS = {
    REASON_COMPLETED: '正常完成',
    REASON_INTERRUPTED: '用户中断 (Ctrl+C)',
    REASON_VERIFICATION: '遇到验证页面 (Verification)',
}


def load_visited_urls(path):
    """读取已访问 URL 记录文件（每行一个 URL）。

    兼容旧文件：自动去除首尾空白，忽略空行与重复行，
    不会因为空行或重复行而崩溃。
    """
    visited = set()
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                url = line.strip()
                if url:
                    visited.add(url)
    return visited


def append_visited_url(path, url):
    """单条 URL 立即追加持久化。

    每成功写入一条 CSV 记录后调用，保证中断时已抓取的 URL 不丢失。
    """
    with open(path, 'a', encoding='utf-8') as f:
        f.write(url + '\n')


def save_visited_urls(path, urls):
    """收尾时整体回写已访问集合（每行一个 URL，去重、无空行）。"""
    with open(path, 'w', encoding='utf-8') as f:
        for url in sorted(urls):
            url = url.strip()
            if url:
                f.write(url + '\n')


class CrawlStats:
    """单次运行的进度统计与结束原因。"""

    def __init__(self):
        self.new_count = 0      # 本次新增抓取条数
        self.skipped_count = 0  # 本次跳过（已访问）条数
        self.reason = REASON_COMPLETED

    def mark_interrupted(self):
        # 验证页原因优先于用户中断，不被覆盖
        if self.reason == REASON_COMPLETED:
            self.reason = REASON_INTERRUPTED

    def mark_verification(self):
        self.reason = REASON_VERIFICATION


def print_summary(stats):
    """在终端输出本次运行的真实进度与结束原因。"""
    reason_label = _REASON_LABELS.get(stats.reason, stats.reason)
    print("\n===== 本次运行汇总 =====")
    print(f"新增抓取: {stats.new_count} 条")
    print(f"跳过(已访问): {stats.skipped_count} 条")
    print(f"结束原因: {reason_label}")
