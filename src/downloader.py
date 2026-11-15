# -*- coding: utf-8 -*-
"""编排层：CDP 连接 → 抓目录 → 逐章「取→解→存」→ 导出 TXT。

反爬：默认串行单标签 + 章间随机延迟抖动。撞验证码时 extractor 会暂停等
人工过码。断点续传：已在 JSON 中间态里的章节跳过。

用法:
  python downloader.py --novel <小说ID或/page/链接>
  python downloader.py --novel <id> --start 1 --end 50      # 指定章节范围
  python downloader.py --novel <id> --split                 # 额外导出分章 TXT
"""
import argparse
import random
import sys
import time

import browser as B
import catalog as C
import storage as S
import extractor
from config import DELAY_RANGE
from extractor import extract_chapter, ChapterExtractError


def _sleep_jitter(lo_hi=DELAY_RANGE) -> None:
    """章间随机延迟，降低风控命中率。"""
    time.sleep(random.uniform(*lo_hi))


def _select_range(chapters: list, start: int, end: int) -> list:
    """按 1-based 章节序号切片（含端点）。start/end 为 None 表示不限。"""
    lo = start if start else 1
    hi = end if end else len(chapters)
    return [c for c in chapters if lo <= c['index'] <= hi]


def _log(msg: str) -> None:
    """统一输出，强制 UTF-8，避免 Windows 控制台 GBK 编码报错。"""
    sys.stdout.buffer.write((msg + '\n').encode('utf-8', 'replace'))
    sys.stdout.buffer.flush()


def download_novel(novel_ref: str, start=None, end=None, split=False,
                   save_every=5, progress=None, stop_event=None) -> dict:
    """按 ID/链接抓整本（或指定章节范围）。返回最终中间态 state。"""
    novel_id = C.parse_novel_id(novel_ref)
    if not novel_id:
        raise SystemExit(f'无法从 {novel_ref!r} 解析出小说ID。')
    return _run(novel_id, start=start, end=end, split=split,
                save_every=save_every, progress=progress,
                stop_event=stop_event)


def download_attach(split=False, save_every=5,
                    progress=None, stop_event=None) -> dict:
    """接管当前已打开的番茄阅读页，从该章所在小说、该章起往后抓。"""
    p, browser, context = B.connect()
    try:
        pages = B.find_fanqie_pages(context)
        reader_pages = [pg for pg in pages if '/reader/' in (pg.url or '')]
        if not reader_pages:
            raise SystemExit('未发现已打开的番茄阅读页(/reader/…)，'
                             '请先在专用 Chrome 里翻到某一章。')
        page = reader_pages[0]
        chapter_id = extractor.chapter_id_from_url(page.url)
        novel_id = extractor.novel_id_from_page(page)
        if not novel_id:
            raise SystemExit(f'无法从当前页解析出小说ID：{page.url}')
        _log(f'接管当前页：小说 {novel_id}，当前章 {chapter_id}')
    finally:
        browser.close()
        p.stop()

    # 用当前 chapter_id 定位在目录里的序号，作为抓取起点。
    cat = C.fetch_catalog(novel_id)
    start_index = next((c['index'] for c in cat['chapters']
                        if c['id'] == chapter_id), 1)
    _log(f'从第 {start_index} 章起往后抓。')
    return _run(novel_id, start=start_index, end=None, split=split,
                save_every=save_every, _cat=cat, progress=progress,
                stop_event=stop_event)


def _emit(progress, **event) -> None:
    """向可选的进度回调发送结构化事件；无回调时静默。"""
    if progress is not None:
        try:
            progress(event)
        except Exception:  # noqa: BLE001 回调异常不应影响抓取
            pass


def _run(novel_id: str, start=None, end=None, split=False, save_every=5,
         _cat=None, progress=None, stop_event=None) -> dict:
    """核心抓取循环：目录 → 逐章「取→解→存」→ 导出。断点续传跳过已抓章。

    progress: 可选回调，收结构化事件(dict)，供服务端上报进度。
    stop_event: 可选 threading.Event，置位后循环在下一章前优雅停止。
    """
    cat = _cat or C.fetch_catalog(novel_id)
    _log(f"《{cat['title']}》共 {len(cat['chapters'])} 章")

    targets = _select_range(cat['chapters'], start, end)
    state = S.load_state(cat['title'], novel_id)
    already = S.done_ids(state)
    todo = [c for c in targets if c['id'] not in already]
    done_cnt = len(targets) - len(todo)
    _log(f'目标 {len(targets)} 章，已抓 {done_cnt} 章，待抓 {len(todo)} 章。')
    _emit(progress, phase='catalog', title=cat['title'], novel_id=novel_id,
          total=len(cat['chapters']), target=len(targets),
          already=done_cnt, todo=len(todo))

    if not todo:
        _log('目标范围已全部抓取，直接导出。')
        return _finish(state, split, progress)

    p, browser, context = B.connect()
    fetched = 0
    total = len(cat['chapters'])
    try:
        page = B.get_or_open_page(context)
        for i, ch in enumerate(todo, 1):
            if stop_event is not None and stop_event.is_set():
                _log('\n收到停止请求，保存已抓进度后退出（下次可续传）。')
                _emit(progress, phase='stopped', fetched=fetched)
                break
            try:
                content = extract_chapter(page, ch['id'])
            except ChapterExtractError as e:
                _log(f"  ✗ [{ch['index']}] {ch['title']} 抽取失败：{e}")
                _emit(progress, phase='chapter_fail', index=ch['index'],
                      title=ch['title'], error=str(e), i=i, todo=len(todo))
                continue
            S.put_chapter(state, ch, content)
            fetched += 1
            _log(f"  ✓ [{ch['index']}/{total}] "
                 f"{ch['title']}  ({len(content)} 字)  进度 {i}/{len(todo)}")
            _emit(progress, phase='chapter', index=ch['index'],
                  title=ch['title'], chars=len(content), i=i,
                  todo=len(todo), total=total)

            if fetched % save_every == 0:
                S.save_state(state)
            if i < len(todo):
                _sleep_jitter()
    except KeyboardInterrupt:
        _log('\n收到中断，保存已抓进度后退出（下次可续传）。')
    finally:
        S.save_state(state)
        browser.close()
        p.stop()

    _log(f'本次新抓 {fetched} 章，中间态已保存。')
    return _finish(state, split, progress)


def _finish(state: dict, split: bool, progress=None) -> dict:
    """导出 TXT 交付物。"""
    single = S.export_single_txt(state)
    if single:
        _log(f'单文件 TXT: {single}')
    split_dir = None
    if split:
        split_dir = S.export_split_txt(state)
        if split_dir:
            _log(f'分章 TXT 目录: {split_dir}')
    _emit(progress, phase='done', title=state['title'],
          novel_id=state['novel_id'],
          chapters=sum(1 for c in state['chapters'].values()
                       if c.get('content')),
          single_txt=single, split_dir=split_dir)
    return state


def main():
    ap = argparse.ArgumentParser(description='番茄小说 CDP 下载器')
    ap.add_argument('--novel', help='小说ID或 /page/ 链接')
    ap.add_argument('--attach', action='store_true',
                    help='接管当前已打开的番茄阅读页，从该章起往后抓')
    ap.add_argument('--start', type=int, help='起始章节序号(1-based，含)')
    ap.add_argument('--end', type=int, help='结束章节序号(含)')
    ap.add_argument('--split', action='store_true', help='额外导出分章 TXT')
    ap.add_argument('--save-every', type=int, default=5,
                    help='每抓 N 章落盘一次中间态(默认5)')
    args = ap.parse_args()

    if args.attach:
        download_attach(args.split, args.save_every)
    elif args.novel:
        download_novel(args.novel, args.start, args.end, args.split,
                       args.save_every)
    else:
        ap.error('请指定 --novel <ID/链接> 或 --attach')


if __name__ == '__main__':
    main()
