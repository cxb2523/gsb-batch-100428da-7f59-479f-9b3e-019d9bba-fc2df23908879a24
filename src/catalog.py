# -*- coding: utf-8 -*-
"""章节目录：从小说主页解析书名与章节列表。

目录页(/page/{id})实测仍可直接抓取，故用轻量 requests 获取；
仅正文需要走浏览器渲染。
"""
import re
from typing import Optional
from config import FANQIE_URL

_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
}


def parse_novel_id(raw) -> Optional[str]:
    """从 URL 或纯数字里解析出 novel_id。"""
    s = str(raw).strip()
    m = re.search(r'/page/(\d+)', s)
    if m:
        return m.group(1)
    m = re.search(r'(\d{6,})', s)
    return m.group(1) if m else None


def fetch_catalog(novel_id: str) -> dict:
    """返回 {'title', 'chapters': [{'index','title','id'}...]}。失败抛异常。"""
    import requests as req
    from lxml import etree

    url = f'{FANQIE_URL}/page/{novel_id}'
    resp = req.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    ele = etree.HTML(resp.text)

    a_elements = ele.xpath('//div[@class="chapter"]/div/a')
    chapters = []
    for a in a_elements:
        href = a.xpath('@href')
        text = ''.join(a.xpath('.//text()')).strip()
        if not href:
            continue
        cid = href[0].rstrip('/').split('/')[-1]
        if cid.isdigit():
            chapters.append({'index': len(chapters) + 1,
                             'title': text or f'第{len(chapters)+1}章',
                             'id': cid})

    title_nodes = ele.xpath('//h1/text()')
    title = title_nodes[0].strip() if title_nodes else f'novel_{novel_id}'
    if not chapters:
        raise RuntimeError(f'未解析到章节，可能页面结构已变: {url}')
    return {'title': title, 'novel_id': novel_id, 'chapters': chapters}

