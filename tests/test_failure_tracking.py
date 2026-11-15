# -*- coding: utf-8 -*-
"""失败章追踪的验收测试（不依赖真实浏览器/CDP）。

用打桩的 extract_chapter 模拟部分章抛 ChapterExtractError，验证：
  1. 失败章写入 failures（含原因+时间），不写 content，不计入已完成；
  2. 重跑时仅失败章被重试，成功后就地清除失败标记；
  3. 旧格式 content.json（无 failures 字段）能正常加载与续传；
  4. 原子写盘不残留 .tmp，落盘 JSON 合法。

运行：python -I tests/test_failure_tracking.py   （或 python -m unittest）
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'src'))

import storage as S           # noqa: E402
import downloader as D        # noqa: E402
from extractor import ChapterExtractError  # noqa: E402

TITLE = '测试书'
NOVEL_ID = 'testnovel001'
CHAPTERS = [{'id': f'c{i}', 'index': i, 'title': f'第{i}章'}
            for i in range(1, 6)]
CAT = {'title': TITLE, 'novel_id': NOVEL_ID, 'chapters': CHAPTERS}


class _FakeConn:
    """顶替 browser.connect() 的返回值 (playwright, browser, context)。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def stop(self):
        self.closed = True


def _make_state(tmpdir, chapters_with_content, failures=None):
    """在临时目录里手写一份 content.json（可指定旧格式：无 failures 字段）。"""
    state = {'title': TITLE, 'novel_id': NOVEL_ID, 'chapters': {}}
    for c in chapters_with_content:
        state['chapters'][c['id']] = {
            'index': c['index'], 'title': c['title'],
            'content': f'{c["title"]}正文内容'}
    if failures is not None:
        state['failures'] = failures
    d = S.novel_dir(TITLE, NOVEL_ID)
    with open(S.state_path(TITLE, NOVEL_ID), 'w', encoding='UTF-8') as f:
        json.dump(state, f, ensure_ascii=False)
    return state


class FailureTrackingTest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix='fqtest_')
        self._orig_output = S.OUTPUT_DIR
        S.OUTPUT_DIR = self._tmpdir
        # 记录每次 extract 调用，便于断言“只重试失败章”
        self.extract_calls = []
        self.fail_ids = set()
        self._patch()

    def tearDown(self):
        S.OUTPUT_DIR = self._orig_output
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _patch(self):
        self._orig = {
            'connect': D.B.connect,
            'get_or_open_page': D.B.get_or_open_page,
            'extract_chapter': D.extract_chapter,
            'sleep_jitter': D._sleep_jitter,
        }
        D.B.connect = lambda: (_FakeConn(), _FakeConn(), object())
        D.B.get_or_open_page = lambda ctx: object()
        D._sleep_jitter = lambda *a, **k: None

        def fake_extract(page, chapter_id):
            self.extract_calls.append(chapter_id)
            if chapter_id in self.fail_ids:
                raise ChapterExtractError(
                    f'章节 {chapter_id} 未抽到正文（模拟）')
            return f'{chapter_id} 的正文内容，足够长。'

        D.extract_chapter = fake_extract
        self.addCleanup(self._unpatch)

    def _unpatch(self):
        D.B.connect = self._orig['connect']
        D.B.get_or_open_page = self._orig['get_or_open_page']
        D.extract_chapter = self._orig['extract_chapter']
        D._sleep_jitter = self._orig['sleep_jitter']

    def _run(self):
        return D._run(NOVEL_ID, _cat=CAT, save_every=2)

    def _state_on_disk(self):
        with open(S.state_path(TITLE, NOVEL_ID), 'r', encoding='UTF-8') as f:
            return json.load(f)

    # ---- 1. 失败章记录与不计入完成 ----
    def test_failures_recorded_not_done(self):
        self.fail_ids = {'c2', 'c4'}
        state = self._run()

        # 失败章不写 content、不算已完成
        self.assertEqual(S.done_ids(state), {'c1', 'c3', 'c5'})
        for cid in ('c2', 'c4'):
            self.assertNotIn(cid, state['chapters'])

        # 失败标记含原因与时间
        fails = S.failures(state)
        self.assertEqual(set(fails), {'c2', 'c4'})
        for cid in ('c2', 'c4'):
            self.assertEqual(fails[cid]['index'], int(cid[1]))
            self.assertIn('未抽到正文', fails[cid]['error'])
            self.assertTrue(fails[cid]['time'])

        # 已原子落盘：无 .tmp 残留，磁盘内容与内存一致
        novel_dir = S.novel_dir(TITLE, NOVEL_ID)
        self.assertFalse(os.path.exists(
            S.state_path(TITLE, NOVEL_ID) + '.tmp'))
        disk = self._state_on_disk()
        self.assertEqual(set(disk.get('failures', {})), {'c2', 'c4'})
        self.assertEqual(set(disk['chapters']), {'c1', 'c3', 'c5'})

    # ---- 2. 重跑只重试失败章，成功后清除标记 ----
    def test_retry_only_failed_and_clear_on_success(self):
        self.fail_ids = {'c2', 'c4'}
        self._run()
        self.assertEqual(set(S.failures(S.load_state(TITLE, NOVEL_ID))),
                         {'c2', 'c4'})

        # 第二轮：全部可成功
        self.fail_ids = set()
        self.extract_calls.clear()
        state = self._run()

        # 只重试了 c2、c4（已抓章被断点续传跳过）
        self.assertEqual(sorted(self.extract_calls), ['c2', 'c4'])
        # 成功后就地清除失败标记
        self.assertEqual(S.failures(state), {})
        self.assertEqual(S.done_ids(state),
                         {f'c{i}' for i in range(1, 6)})
        self.assertEqual(self._state_on_disk().get('failures'), {})

    # ---- 3. 失败持续存在时重跑仍重试且标记保留 ----
    def test_persistent_failure_kept(self):
        self.fail_ids = {'c3'}
        self._run()
        self.extract_calls.clear()
        state = self._run()  # 仍失败
        self.assertEqual(self.extract_calls, ['c3'])
        self.assertEqual(set(S.failures(state)), {'c3'})
        self.assertEqual(S.done_ids(state),
                         {'c1', 'c2', 'c4', 'c5'})

    # ---- 4. 旧格式中间态（无 failures 字段）兼容 ----
    def test_legacy_state_loads_and_resumes(self):
        # 手写旧格式：只有 chapters，没有 failures
        _make_state(self._tmpdir, CHAPTERS[:3])
        state = S.load_state(TITLE, NOVEL_ID)
        self.assertEqual(S.failures(state), {})
        self.assertEqual(S.done_ids(state), {'c1', 'c2', 'c3'})

        # 续传：只抓 c4、c5；c4 失败 → 记 failures；旧数据不受影响
        self.fail_ids = {'c4'}
        state = self._run()
        self.assertEqual(sorted(self.extract_calls), ['c4', 'c5'])
        self.assertEqual(S.done_ids(state), {'c1', 'c2', 'c3', 'c5'})
        self.assertEqual(set(S.failures(state)), {'c4'})

        # 再跑：仅重试 c4，成功后标记清除
        self.fail_ids = set()
        self.extract_calls.clear()
        state = self._run()
        self.assertEqual(self.extract_calls, ['c4'])
        self.assertEqual(S.failures(state), {})
        self.assertEqual(len(S.done_ids(state)), 5)

    # ---- 5. 旧格式里带失败标记的混合情形：失败章不算已完成 ----
    def test_failed_chapter_not_counted_as_done(self):
        _make_state(self._tmpdir, CHAPTERS[:2],
                    failures={'c3': {'index': 3, 'title': '第3章',
                                     'error': '模拟失败',
                                     'time': '2026-11-15T10:00:00'}})
        state = S.load_state(TITLE, NOVEL_ID)
        self.assertEqual(S.done_ids(state), {'c1', 'c2'})
        self.assertEqual(S.failed_ids(state), {'c3'})
        # 续传会把 c3 当作待抓重试
        self.extract_calls.clear()
        state = self._run()
        self.assertEqual(sorted(self.extract_calls), ['c3', 'c4', 'c5'])
        self.assertEqual(S.failures(state), {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
