#-*- coding: utf-8 -*-
"""
spider.py 的离线分段采集测试。

不依赖任何真实教务网：用 FakeSession 按 URL 路由返回 tests/samples 下的
自造 HTML，或抛出 requests 层异常，验证：
  1. 三段互不影响，某段失败其余段仍采集并落盘；
  2. 分段结局含段名、成功/失败/未执行及原因，且原因能区分
     登录失效 / 页面结构不符 / 网络请求异常；
  3. 解析失败的段不改动已有内容，且不会留下半截表格；
  4. 登录失败时三段均为“未执行”，退出码非零。

运行（仓库根目录）：
    python -I tests/test_spider.py
"""

import builtins
import io
import os
import sys
import shutil
import tempfile
import unittest

import requests
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SAMPLES = os.path.join(HERE, 'samples')

sys.path.insert(0, REPO_ROOT)
import spider  # noqa: E402


def sample_text(name):
    with open(os.path.join(SAMPLES, name), 'rb') as f:
        return f.read()


class FakeResponse:
    def __init__(self, content, text_encoding='utf-8'):
        self.content = content
        try:
            self.text = content.decode(text_encoding)
        except UnicodeDecodeError:
            # 二进制内容（如验证码 JPEG）没有可用的文本
            self.text = None


class FakeSession:
    """按 URL 片段路由的假 requests.Session；路由值可以是 HTML 或异常实例。"""

    def __init__(self, routes):
        # routes: list of (url_fragment, payload)
        self.routes = routes
        self.headers = {}
        self.calls = []

    def _dispatch(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for fragment, payload in self.routes:
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        raise AssertionError('unexpected %s url: %s' % (method, url))

    def get(self, url, **kwargs):
        return self._dispatch('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self._dispatch('POST', url, **kwargs)


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new('RGB', (16, 8), color=(120, 120, 120)).save(buf, 'JPEG')
    return buf.getvalue()


JPEG_BYTES = jpeg_bytes()


def build_routes(overrides=None):
    """默认全部正常的路由表，overrides 用来替换某一段的行为。"""
    routes = [
        ('CheckCode.aspx', JPEG_BYTES),
        ('default2.aspx', sample_text('login.html')),
        ('default2.aspx#POST', sample_text('login_success.html')),
        ('xskbcx.aspx', sample_text('class_ok.html')),
        ('xscjcx.aspx', sample_text('grade_form.html')),
        # 历年成绩段会先 GET 再 POST 同 URL，靠调用次序区分
        ('xscjcx.aspx#POST', sample_text('grade_ok.html')),
        ('xsdjkscx.aspx', sample_text('test_ok.html')),
    ]
    if overrides:
        for frag, payload in overrides.items():
            for i, (f, _p) in enumerate(routes):
                if f == frag:
                    routes[i] = (f, payload)
                    break
            else:
                routes.append((frag, payload))
    return routes


class PostAwareSession(FakeSession):
    """xscjcx.aspx 同 URL 有 GET/POST 两次请求，按方法区分返回内容。"""

    def _dispatch(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for fragment, payload in self.routes:
            base = fragment[:-5] if fragment.endswith('#POST') else fragment
            if base in url:
                if fragment.endswith('#POST') and method != 'POST':
                    continue
                if not fragment.endswith('#POST') and method == 'POST' \
                        and any(f == base + '#POST' for f, _ in self.routes):
                    continue
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        raise AssertionError('unexpected %s url: %s' % (method, url))


class SpiderSegmentTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='zf-test-')
        self.out = os.path.join(self.tmp, 'zhengfang.txt')
        self.code = os.path.join(self.tmp, 'code.jpg')
        self._orig_input = builtins.input
        # 验证码输入固定为 1234
        builtins.input = lambda *a, **k: '1234'

    def tearDown(self):
        builtins.input = self._orig_input
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_univ(self, routes):
        session = PostAwareSession(routes)
        who = spider.Who('201600010001', 'secret')
        univ = spider.University(
            who, 'http://mock.edu.cn', session=session,
            code_path=self.code, output_path=self.out)
        # 避免真的弹出图片查看器
        orig_show = Image.Image.show
        Image.Image.show = lambda self: None
        self.addCleanup(setattr, Image.Image, 'show', orig_show)
        return univ

    def read_out(self):
        with open(self.out, 'r', encoding='utf-8') as f:
            return f.read()

    # -- 1. 全部成功 -------------------------------------------------------

    def test_all_segments_success_exit_zero(self):
        univ = self.make_univ(build_routes())
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_SUCCESS)
        text = self.read_out()

        self.assertIn(u'本学期课表:', text)
        self.assertIn(u'高等数学A', text)
        self.assertIn(u'历年成绩:', text)
        self.assertIn(u'平均绩点:', text)
        self.assertIn(u'等级考试成绩:', text)
        self.assertIn(u'英语四级', text)

        self.assertIn(u'采集分段结局:', text)
        self.assertIn(u'[课表] 成功', text)
        self.assertIn(u'[历年成绩] 成功', text)
        self.assertIn(u'[等级考试成绩] 成功', text)

        # 临时文件应被原子替换清理掉
        leftovers = [n for n in os.listdir(self.tmp)
                     if n.startswith('.zhengfang.')]
        self.assertEqual(leftovers, [])

    # -- 2. 单段结构损坏，其余段不受影响 ----------------------------------

    def test_broken_grade_keeps_other_segments(self):
        routes = build_routes({
            'xscjcx.aspx#POST': sample_text('grade_broken.html'),
        })
        univ = self.make_univ(routes)
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_SEGMENT_FAILED)
        text = self.read_out()

        self.assertIn(u'高等数学A', text)          # 课表段成功
        self.assertIn(u'英语四级', text)          # 等级考试段成功
        self.assertNotIn(u'历年成绩:', text)       # 失败段不写任何半截内容
        self.assertIn(u'[历年成绩] 失败 - 失败原因：页面结构不符', text)
        self.assertIn(u'[课表] 成功', text)
        self.assertIn(u'[等级考试成绩] 成功', text)

    def test_broken_class_broken_test_two_failures(self):
        routes = build_routes({
            'xskbcx.aspx': sample_text('class_broken.html'),
            'xsdjkscx.aspx': sample_text('test_broken.html'),
        })
        univ = self.make_univ(routes)
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_SEGMENT_FAILED)
        text = self.read_out()

        self.assertIn(u'[课表] 失败 - 失败原因：页面结构不符', text)
        self.assertIn(u'[等级考试成绩] 失败 - 失败原因：页面结构不符', text)
        self.assertIn(u'[历年成绩] 成功', text)
        # 两个失败段都没有留下标题或半截表格
        self.assertNotIn(u'本学期课表:', text)
        self.assertNotIn(u'等级考试成绩:', text)

    # -- 3. 网络请求异常被正确归类，且不影响其他段 ------------------------

    def test_network_error_in_one_segment(self):
        routes = build_routes({
            'xskbcx.aspx': requests.ConnectionError('mock: connection refused'),
        })
        univ = self.make_univ(routes)
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_SEGMENT_FAILED)
        text = self.read_out()

        self.assertIn(u'[课表] 失败 - 失败原因：网络请求异常', text)
        self.assertIn(u'[历年成绩] 成功', text)
        self.assertIn(u'[等级考试成绩] 成功', text)
        self.assertNotIn(u'本学期课表:', text)

    def test_timeout_in_test_results_segment(self):
        routes = build_routes({
            'xsdjkscx.aspx': requests.Timeout('mock: read timed out'),
        })
        univ = self.make_univ(routes)
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_SEGMENT_FAILED)
        text = self.read_out()
        self.assertIn(u'[等级考试成绩] 失败 - 失败原因：网络请求异常', text)
        self.assertIn(u'[课表] 成功', text)
        self.assertIn(u'[历年成绩] 成功', text)

    # -- 4. 登录失效被正确归类 --------------------------------------------

    def test_session_expired_midway(self):
        routes = build_routes({
            'xsdjkscx.aspx': sample_text('session_expired.html'),
        })
        univ = self.make_univ(routes)
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_SEGMENT_FAILED)
        text = self.read_out()
        self.assertIn(u'[等级考试成绩] 失败 - 失败原因：登录失效', text)
        self.assertIn(u'[课表] 成功', text)
        self.assertIn(u'[历年成绩] 成功', text)

    # -- 5. 解析失败不覆盖该段旧内容，成功段仍刷新 ------------------------

    def test_failed_segment_preserves_previous_content(self):
        # 先制造一份含三段旧内容的 zhengfang.txt
        old_text = (
            u'本学期课表:\nOLD-CLASS\n\n\n'
            u'历年成绩:\nOLD-GRADE-HEADER\nOLD-GRADE-ROW\n\n\n'
            u'等级考试成绩:\nOLD-TEST-HEADER\nOLD-TEST-ROW\n'
            u'\n\n\n采集分段结局:\n[课表] 成功\n'
        )
        with open(self.out, 'w', encoding='utf-8') as f:
            f.write(old_text)

        routes = build_routes({
            'xscjcx.aspx#POST': sample_text('grade_broken.html'),
        })
        univ = self.make_univ(routes)
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_SEGMENT_FAILED)
        text = self.read_out()

        # 课表、等级考试段被本次成功结果刷新
        self.assertIn(u'高等数学A', text)
        self.assertIn(u'英语四级', text)
        self.assertNotIn(u'OLD-CLASS', text)
        self.assertNotIn(u'OLD-TEST-ROW', text)
        # 历年成绩段解析失败，旧内容原样保留，没有半截表格
        self.assertIn(u'历年成绩:', text)
        self.assertIn(u'OLD-GRADE-HEADER', text)
        self.assertIn(u'OLD-GRADE-ROW', text)
        self.assertIn(u'[历年成绩] 失败 - 失败原因：页面结构不符', text)
        # 上一轮的旧结局被本轮结局替换，而不是追加
        self.assertEqual(text.count(u'采集分段结局:'), 1)

    # -- 6. 登录失败：三段全部未执行，退出码非零 --------------------------

    def test_login_failure_marks_all_segments_skipped(self):
        routes = build_routes({
            'default2.aspx#POST': sample_text('login_failed.html'),
        })
        univ = self.make_univ(routes)
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_LOGIN_FAILED)
        self.assertNotEqual(code, 0)
        text = self.read_out()

        self.assertIn(u'[课表] 未执行 - 原因：登录失败', text)
        self.assertIn(u'[历年成绩] 未执行 - 原因：登录失败', text)
        self.assertIn(u'[等级考试成绩] 未执行 - 原因：登录失败', text)
        # 登录失败不会产生任何业务表格
        self.assertNotIn(u'本学期课表:', text)
        self.assertNotIn(u'历年成绩:', text)
        self.assertNotIn(u'等级考试成绩:', text)
        # 登录阶段之后不应发起任何业务请求
        for method, url, _kw in univ.session.calls:
            self.assertNotIn('xskbcx.aspx', url)
            self.assertNotIn('xscjcx.aspx', url)
            self.assertNotIn('xsdjkscx.aspx', url)

    def test_login_network_error_is_nonzero(self):
        routes = build_routes({
            'default2.aspx': requests.ConnectionError('mock: down'),
        })
        univ = self.make_univ(routes)
        code = spider.run(univ)
        self.assertEqual(code, spider.EXIT_LOGIN_FAILED)
        text = self.read_out()
        self.assertIn(u'[课表] 未执行 - 原因：登录失败', text)

    # -- 7. 纯解析函数级别的结构校验 --------------------------------------

    def test_getgrade_raises_structure_error(self):
        with self.assertRaises(spider.StructureError):
            spider.Getgrade(sample_text('test_broken.html'))

    def test_getgradetestresults_raises_structure_error(self):
        html = spider.etree.HTML(sample_text('test_broken.html'))
        trs = html.xpath('//table[@class="datelist"]/tr')
        with self.assertRaises(spider.StructureError):
            spider.Getgradetestresults(trs)

    # -- 8. 落盘内容与仓库原 zhengfang.txt 的表格风格一致 -----------------

    def test_output_text_table_style_matches_original(self):
        univ = self.make_univ(build_routes())
        self.assertEqual(spider.run(univ), spider.EXIT_SUCCESS)
        text = self.read_out()

        # 课表：序号 + 制表符；成绩：定宽列 + 制表符，且一条记录占一行
        self.assertRegex(text, r'1:\t高等数学A')
        self.assertRegex(text, u'学年 +\t.*?绩点 +\t.*?成绩 +\t')
        self.assertRegex(text, u'2020-2021 +\t.*?95 +\t')
        self.assertRegex(text, u'英语四级 +\t.*?563 +\t')
        # 平均绩点行的格式与原文件一致
        self.assertRegex(text, u'平均绩点: \\d+\\.\\d{2}\t\t\t'
                               u'总学分绩点: \\d+\\.\\d{2}\t\t\t'
                               u'总学分: \\d+\\.\\d{2}')

    # -- 9. 二次运行：结局区不重复，失败段旧内容仍保留 --------------------

    def test_rerun_replaces_summary_and_keeps_failed_block(self):
        routes1 = build_routes()
        self.assertEqual(spider.run(self.make_univ(routes1)),
                         spider.EXIT_SUCCESS)

        routes2 = build_routes({
            'xskbcx.aspx': requests.Timeout('mock: timeout again'),
        })
        code = spider.run(self.make_univ(routes2))
        self.assertEqual(code, spider.EXIT_SEGMENT_FAILED)
        text = self.read_out()

        self.assertEqual(text.count(u'采集分段结局:'), 1)
        self.assertIn(u'[课表] 失败 - 失败原因：网络请求异常', text)
        self.assertIn(u'[历年成绩] 成功', text)
        # 上一轮成功写入的课表内容在本轮失败后保持原样
        self.assertIn(u'高等数学A', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
