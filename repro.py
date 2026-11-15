#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日头条主页推送新闻流（Feed）纯协议复现与实时抓取脚本

原理：
  1. 调用本地 Node.js 沙箱运行 bdms.js（JSVMP），结合轻量 DOM/XHR 垫片模拟真实浏览器请求；
  2. 提取 localStorage.xmst 种子（msToken），在 XHR.send() 阶段生成 168 位 a_bogus 签名；
  3. 采用「现签现发」机制在毫秒级内发起 HTTP GET 请求，直接获取包含完整新闻流数据的 JSON 响应。

运行环境：
  - Python 3.8+
  - Node.js >= 18
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Windows 下统一标准输出为 UTF-8，防止控制台中文乱码
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

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


def fetch_toutiao_homepage_news(
    channel_id: int = 0,
    category: str = "pc_profile_recommend",
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    """
    抓取今日头条主页最新推送新闻
    
    :param channel_id: 频道 ID，默认 0（推荐）
    :param category: 分类标识，默认 pc_profile_recommend
    :param timeout: 网络请求超时时间（秒）
    :return: 解析后的结构化新闻列表
    """
    # 1. 构建待签名的原始 URL（不含 a_bogus）
    base_url = build_feed_url(channel_id=channel_id, category=category)
    logger.info("构建原始待签 URL: %s...", base_url[:80])

    # 2. 纯本地 Node JSVMP 离线签名（现签现用）
    signed = sign_abogus(base_url, refresh_snapshot=False)
    signed_url = signed.get("signed_url")
    a_bogus = signed.get("a_bogus", "")
    
    if not signed_url or not a_bogus:
        logger.error("签名生成失败，请检查 Node.js 环境及 bdms.js 是否完整")
        return []

    logger.info("签名成功 (a_bogus 长度: %d)，立刻发起 HTTP 请求...", len(a_bogus))

    # 3. 构造请求头并发送 GET 请求
    headers = {
        "accept": "application/json, text/plain, */*",
        "referer": "https://www.toutiao.com/",
        "user-agent": USER_AGENT,
    }
    req = urllib.request.Request(signed_url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.error("服务端响应状态码异常: %s", resp.status)
                return []
            
            raw_data = resp.read()
            logger.info("成功接收响应体 (大小: %d 字节)", len(raw_data))
            
            payload = json.loads(raw_data.decode("utf-8", "replace"))
            if payload.get("message") != "success":
                logger.warning("接口返回非 success 状态: %s", payload.get("message"))
                return []

            items = payload.get("data", [])
            news_list: List[Dict[str, Any]] = []

            # 4. 解析结构化新闻字段
            for item in items:
                if not isinstance(item, dict):
                    continue
                
                title = item.get("title") or item.get("Abstract")
                if not title:
                    continue

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

                news_list.append({
                    "title": title.strip(),
                    "source": source,
                    "publish_time": pub_time,
                    "url": article_url,
                    "read_count": item.get("read_count", 0),
                    "comments_count": item.get("comments_count", 0),
                    "group_id": str(group_id) if group_id else "",
                })

            return news_list

    except Exception as exc:
        logger.exception("抓取头条新闻流异常: %s", exc)
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description="今日头条主页新闻流抓取复现工具")
    parser.add_argument("--limit", type=int, default=10, help="展示的新闻条数限制")
    parser.add_argument("--save", action="store_true", default=True, help="是否将结果保存至 sample_data.json")
    args = parser.parse_args()

    news = fetch_toutiao_homepage_news()
    if not news:
        logger.error("未获取到新闻数据（可能由于签名过期或风控拦截）")
        return 1

    print(f"\n================ 实时抓取结果 (共 {len(news)} 条) ================")
    for idx, item in enumerate(news[:args.limit], 1):
        print(f"\n[{idx}] 标题: {item['title']}")
        print(f"    来源: {item['source']} | 时间: {item['publish_time']} | 阅读: {item['read_count']} | 评论: {item['comments_count']}")
        print(f"    链接: {item['url']}")
    print("=================================================================\n")

    if args.save:
        SAMPLE_OUTPUT.write_text(json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("完整数据已落盘至: %s", SAMPLE_OUTPUT.name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
