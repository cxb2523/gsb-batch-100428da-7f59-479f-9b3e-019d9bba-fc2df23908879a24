#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取今日头条指定文章的全部用户评论（基于 acrawler _signature JSVMP 纯离线复现）

接口规范：
  GET https://www.toutiao.com/article/v4/tab_comments/
  参数：
    - aid: 24
    - app_name: toutiao_web
    - offset: 分页偏移（0, 20, 40...）
    - count: 每页数量（默认 20）
    - group_id: 文章 ID
    - item_id: 文章 ID
    - _signature: acrawler JSVMP 生成的签名
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Windows 下统一标准输出为 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
REPLAY_JS = HERE / "replay_real.js"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)


def get_signature_for_url(url: str) -> str:
    """调用 Node 离线执行 acrawler.js VM 生成 _signature"""
    proc = subprocess.run(
        ["node", str(REPLAY_JS), url],
        cwd=str(HERE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"acrawler 签名失败: {proc.stderr or proc.stdout}")
    
    # 从输出中提取 SIG : _02B4...
    for line in proc.stdout.splitlines():
        if line.startswith("SIG :"):
            return line.split(":", 1)[1].strip()
    
    raise RuntimeError("未能从 Node 输出中解析出 _signature")


def fetch_comments(
    group_id: str = "7673783789474071075",
    offset: int = 0,
    count: int = 20,
    cookie: str = "",
) -> List[Dict[str, Any]]:
    """
    抓取文章评论列表
    
    :param group_id: 文章 group_id / item_id
    :param offset: 评论分页偏移量
    :param count: 本次拉取评论数
    :param cookie: 可选 Cookie（非必选）
    :return: 结构化评论列表
    """
    base_url = (
        f"https://www.toutiao.com/article/v4/tab_comments/?"
        f"aid=24&app_name=toutiao_web&offset={offset}&count={count}&"
        f"group_id={group_id}&item_id={group_id}"
    )
    
    # 1. 离线生成 _signature
    sig = get_signature_for_url(base_url)
    signed_url = f"{base_url}&_signature={sig}"

    # 2. 发起 HTTP GET
    headers = {
        "accept": "application/json, text/plain, */*",
        "referer": f"https://www.toutiao.com/article/{group_id}/",
        "user-agent": UA,
    }
    if cookie:
        headers["Cookie"] = cookie

    req = urllib.request.Request(signed_url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw_text = resp.read().decode("utf-8", "replace")
        data = json.loads(raw_text)
        
        items = data.get("data", [])
        comment_list: List[Dict[str, Any]] = []

        for it in items:
            c = it.get("comment", {})
            if not c:
                continue
            text = c.get("text", "")
            user_name = c.get("user_name", "匿名用户")
            loc = c.get("publish_loc_info", "未知")
            digg = c.get("digg_count", 0)
            reply = c.get("reply_count", 0)
            c_time = c.get("create_time", 0)
            pub_time = (
                datetime.fromtimestamp(c_time).strftime("%Y-%m-%d %H:%M:%S")
                if c_time
                else ""
            )

            comment_list.append({
                "comment_id": c.get("id_str") or str(c.get("id")),
                "user_name": user_name,
                "location": loc,
                "text": text,
                "digg_count": digg,
                "reply_count": reply,
                "publish_time": pub_time,
            })

        return comment_list


if __name__ == "__main__":
    target_group_id = "7673783789474071075"
    if len(sys.argv) > 1:
        target_group_id = sys.argv[1].strip()
    
    print(f"开始抓取文章 [{target_group_id}] 的评论列表...")
    comments = fetch_comments(group_id=target_group_id, offset=0, count=20)
    print(f"成功获取到 {len(comments)} 条热门评论：\n")
    for idx, c in enumerate(comments, 1):
        print(f"{idx}. [{c['user_name']}] ({c['location']}) {c['publish_time']}")
        print(f"   内容: {c['text']}")
        print(f"   👍 点赞: {c['digg_count']} | 💬 回复: {c['reply_count']}")
        print("-" * 60)
