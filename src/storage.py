# -*- coding: utf-8 -*-
"""存储层：JSON 中间态（断点续传） + TXT 输出（单文件 / 分章）。

JSON 中间态是唯一权威数据源，抓取过程只往里增量写；TXT 是从 JSON 导出的
交付物。断点续传靠 JSON 里已有的章节 id 集合判断哪些章还没抓。
"""
import json
import os
import re
from datetime import datetime
from typing import Optional
from config import OUTPUT_DIR

_ILLEGAL_FS = re.compile(r'[\\/:*?"<>|]')

# 中文小说排版：每段首行缩进两个全角空格，段与段之间空一行。
# JSON 里 content 是各段用单个 \n 拼接的裸文本，导出时才做这层表现处理。
_PARA_INDENT = '　　'


def _format_body(content: str) -> str:
    """把裸正文（\\n 分段）排成中文阅读习惯：段首缩进，段间不空行、正常行距。"""
    paragraphs = [ln.strip() for ln in content.split('\n')]
    return '\n'.join(f'{_PARA_INDENT}{p}' for p in paragraphs if p)


def _safe_name(name: str) -> str:
    """把书名/章节名清成合法文件名。"""
    cleaned = _ILLEGAL_FS.sub('_', name).strip().rstrip('.')
    return cleaned[:120] or 'untitled'


def novel_dir(title: str, novel_id: str) -> str:
    """每本书一个独立目录：output/<书名>_<id>/。"""
    d = os.path.join(OUTPUT_DIR, f'{_safe_name(title)}_{novel_id}')
    os.makedirs(d, exist_ok=True)
    return d


def state_path(title: str, novel_id: str) -> str:
    return os.path.join(novel_dir(title, novel_id), 'content.json')


def load_state(title: str, novel_id: str) -> dict:
    """读已抓的 JSON 中间态；不存在则返回空壳。

    兼容旧格式：早期 content.json 没有 failures 字段，读出后补空表，
    按「无失败」处理。
    """
    path = state_path(title, novel_id)
    if os.path.exists(path):
        with open(path, 'r', encoding='UTF-8') as f:
            state = json.load(f)
        state.setdefault('failures', {})
        return state
    return {'title': title, 'novel_id': novel_id,
            'chapters': {}, 'failures': {}}


def save_state(state: dict) -> str:
    """把中间态原子写回磁盘（先写 tmp 再 replace，防写一半损坏）。"""
    path = state_path(state['title'], state['novel_id'])
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='UTF-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def done_ids(state: dict) -> set:
    """已成功抓到正文的章节 id 集合（用于断点续传）。

    失败章只进 failures、不写 content，因此天然不会被算作已完成。
    """
    return {cid for cid, ch in state['chapters'].items() if ch.get('content')}


def put_chapter(state: dict, chapter: dict, content: str) -> dict:
    """记录一章正文到中间态（不落盘，由调用方决定何时 save_state）。

    成功即就地清除该章的失败标记（若有）。
    """
    state['chapters'][chapter['id']] = {
        'index': chapter['index'],
        'title': chapter['title'],
        'content': content,
    }
    clear_failure(state, chapter['id'])
    return state


def put_failure(state: dict, chapter: dict, error) -> dict:
    """记录一章抽取失败：写失败标记（原因+时间），不写 content。

    不落盘，由调用方决定何时 save_state。失败章不进 chapters，
    断点续传时会被重新尝试。
    """
    failures = state.setdefault('failures', {})
    failures[chapter['id']] = {
        'index': chapter['index'],
        'title': chapter['title'],
        'error': str(error),
        'time': datetime.now().isoformat(timespec='seconds'),
    }
    return state


def clear_failure(state: dict, chapter_id: str) -> None:
    """清除某章的失败标记（无标记时静默）。"""
    failures = state.get('failures')
    if failures:
        failures.pop(chapter_id, None)


def failures(state: dict) -> dict:
    """当前失败章表 {chapter_id: {index,title,error,time}}；旧格式视为无失败。"""
    return state.get('failures') or {}


def failed_ids(state: dict) -> set:
    """当前有失败标记的章节 id 集合。"""
    return set(failures(state))


def _ordered_chapters(state: dict) -> list:
    """按 index 升序返回已抓章节。"""
    items = [c for c in state['chapters'].values() if c.get('content')]
    return sorted(items, key=lambda c: c['index'])


def export_single_txt(state: dict) -> Optional[str]:
    """导出单文件 TXT：<书名>.txt，章节间用标题分隔。"""
    chapters = _ordered_chapters(state)
    if not chapters:
        return None
    d = novel_dir(state['title'], state['novel_id'])
    path = os.path.join(d, f"{_safe_name(state['title'])}.txt")
    with open(path, 'w', encoding='UTF-8') as f:
        f.write(f"{state['title']}\n\n")
        for ch in chapters:
            f.write(f"{ch['title']}\n\n{_format_body(ch['content'])}\n\n\n")
    return path


def export_split_txt(state: dict) -> Optional[str]:
    """导出分章 TXT：<书名>_chapters/NNNN_章节名.txt。返回目录。"""
    chapters = _ordered_chapters(state)
    if not chapters:
        return None
    d = os.path.join(novel_dir(state['title'], state['novel_id']),
                     f"{_safe_name(state['title'])}_chapters")
    os.makedirs(d, exist_ok=True)
    for ch in chapters:
        fname = f"{ch['index']:04d}_{_safe_name(ch['title'])}.txt"
        with open(os.path.join(d, fname), 'w', encoding='UTF-8') as f:
            f.write(f"{ch['title']}\n\n{_format_body(ch['content'])}\n")
    return d
