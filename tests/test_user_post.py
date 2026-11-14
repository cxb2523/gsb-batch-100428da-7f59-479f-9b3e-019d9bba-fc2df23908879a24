# -*- coding: utf-8 -*-
"""「采集主页作品」链路验收测试

用本地假 session 驱动，不访问真实抖音网络。覆盖：
1. 正常多页采集后 CSV 行数与页数一致（逐页落盘）
2. 第二页请求抛异常时第一页数据仍在文件中，且失败原因可见
3. 重跑同一博主时不重复追加第一页，只补采失败页
4. 下载开关关闭时无下载调用
"""
import csv
import json
import logging
import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import douyin_user_post
from douyin_user_post import DouyinUserPosted

SEC_UID = 'MS4wLjABAAAAfake_sec_uid_0001'
USER_LINK = 'https://www.douyin.com/user/' + SEC_UID


def make_aweme(idx):
    """构造一条假作品数据"""
    return {
        'aweme_id': f'700000000000000{idx:04d}',
        'desc': f'测试视频标题{idx}',
        'create_time': 1700000000 + idx,
        'is_top': 1 if idx == 1 else 0,
        'author': {
            'nickname': '测试博主',
            'uid': '123456789',
            'sec_uid': SEC_UID,
            'follower_count': 8888,
        },
        'text_extra': [{'hashtag_name': '测试话题'}],
        'video': {
            'duration': 15300,
            'play_addr': {'url_list': ['https://aweme.snssdk.com/playwm/video/abc/']},
        },
        'statistics': {
            'digg_count': 100 + idx,
            'comment_count': 10 + idx,
            'collect_count': 5 + idx,
            'recommend_count': 2 + idx,
            'share_count': 1 + idx,
        },
    }


def make_page(start_idx, count, has_more):
    return {
        'status_code': 0,
        'has_more': 1 if has_more else 0,
        'aweme_list': [make_aweme(start_idx + i) for i in range(count)],
    }


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Error')


class FakeSession:
    """假 session：按 max_cursor 返回预置页数据或抛出预置异常"""

    def __init__(self, pages):
        self.pages = pages          # {max_cursor: dict 或 Exception 实例}
        self.calls = []             # 记录每次请求的 max_cursor
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        cursor = params['max_cursor']
        self.calls.append(cursor)
        result = self.pages[cursor]
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


class FakeText:
    """模拟 Tkinter Text 控件"""

    def delete(self, *a):
        pass

    def insert(self, *a):
        pass

    def see(self, *a):
        pass

    def bell(self):
        pass

    def after(self, *a):
        pass


class UserPostTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(__import__('tempfile').TemporaryDirectory())
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self._cwd)
        # 假 cookie 与配置
        with open('cookie.txt', 'w', encoding='utf8') as f:
            f.write('sessionid=fake_session_id;')
        with open('config_pub.json', 'w', encoding='utf8') as f:
            json.dump({'wait_sec': 1}, f)
        # 屏蔽 sleep 与弹窗
        self.enterContext(mock.patch('time.sleep'))
        self.enterContext(mock.patch.object(douyin_user_post.messagebox, 'showinfo'))
        self.enterContext(mock.patch.object(douyin_user_post.messagebox, 'showerror'))
        self.logger = logging.getLogger('test_user_post')
        self.logger.addHandler(logging.NullHandler())

    def build_spider(self, session, top_num=3, down_tag=False):
        spider = DouyinUserPosted([USER_LINK], top_num, down_tag,
                                  FakeText(), self.logger, session=session)
        spider.page_size = 1  # 每页1条，页数=条数，便于断言
        return spider

    def read_rows(self, result_file):
        with open(result_file, 'r', encoding='utf_8_sig', newline='') as f:
            return list(csv.reader(f))


class TestNormalMultiPage(UserPostTestBase):
    def test_multi_page_csv_rows_match_pages(self):
        """正常多页采集：CSV 数据行数与页数一致，且为逐页追加"""
        session = FakeSession({
            0: make_page(1, 1, has_more=True),
            1: make_page(2, 1, has_more=True),
            2: make_page(3, 1, has_more=False),
        })
        spider = self.build_spider(session, top_num=3)
        spider.get_user_post()

        rows = self.read_rows(spider.result_file)
        header, data_rows = rows[0], rows[1:]
        self.assertEqual(len(header), 17)
        self.assertEqual(len(data_rows), 3)  # 3页 x 每页1条
        self.assertEqual([r[0] for r in data_rows], ['1', '2', '3'])  # 页码列
        self.assertEqual(data_rows[0][1], '测试博主')      # 作者昵称
        self.assertEqual(data_rows[0][3], SEC_UID)         # sec_uid
        self.assertEqual(data_rows[0][11], '是')           # 第1条置顶
        # 三页都被请求过
        self.assertEqual(session.calls, [0, 1, 2])
        # 进度文件记录全部成功页
        with open(spider.progress_file, encoding='utf8') as f:
            progress = json.load(f)
        self.assertEqual(progress[SEC_UID]['success_pages'], [1, 2, 3])
        self.assertEqual(progress[SEC_UID]['failed_pages'], {})


class TestPageFailure(UserPostTestBase):
    def test_page2_exception_keeps_page1_and_shows_reason(self):
        """第二页请求抛异常：第一页数据仍在文件中，失败原因可见，剩余页继续"""
        session = FakeSession({
            0: make_page(1, 1, has_more=True),
            1: requests.ConnectionError('模拟网络断开'),
            2: make_page(3, 1, has_more=False),
        })
        spider = self.build_spider(session, top_num=3)
        spider.get_user_post()

        rows = self.read_rows(spider.result_file)[1:]
        # 第1页数据仍在文件中，第3页继续采集成功
        self.assertEqual([r[0] for r in rows], ['1', '3'])
        # 失败原因在界面日志与进度文件中均可判别
        joined = '\n'.join(spider.describe)
        self.assertIn('网络异常', joined)
        self.assertIn('第2页', joined)
        with open(spider.progress_file, encoding='utf8') as f:
            record = json.load(f)[SEC_UID]
        self.assertIn('网络异常', record['failed_pages']['2'])
        self.assertEqual(record['success_pages'], [1, 3])

    def test_empty_and_risk_control_pages_are_classified(self):
        """接口返回空与验证码/风控有各自可判别的原因"""
        session = FakeSession({
            0: {'status_code': 0, 'has_more': 1, 'aweme_list': []},          # 空列表
            1: {'status_code': 2154, 'captcha': {'verify_url': 'https://x'}},  # 风控
            2: make_page(3, 1, has_more=False),
        })
        spider = self.build_spider(session, top_num=3)
        spider.get_user_post()

        with open(spider.progress_file, encoding='utf8') as f:
            record = json.load(f)[SEC_UID]
        self.assertIn('接口返回空', record['failed_pages']['1'])
        self.assertIn('验证码/风控', record['failed_pages']['2'])
        self.assertEqual(record['success_pages'], [3])
        joined = '\n'.join(spider.describe)
        self.assertIn('接口返回空', joined)
        self.assertIn('验证码/风控', joined)


class TestResume(UserPostTestBase):
    def test_rerun_skips_success_pages_and_fills_failed(self):
        """重跑同一博主：已成功页不重复请求、不重复追加，只补采失败页"""
        # 第一次运行：第2页网络异常
        session1 = FakeSession({
            0: make_page(1, 1, has_more=True),
            1: requests.ConnectionError('模拟网络断开'),
            2: make_page(3, 1, has_more=False),
        })
        spider1 = self.build_spider(session1, top_num=3)
        spider1.get_user_post()
        result_file = spider1.result_file
        rows_after_run1 = self.read_rows(result_file)[1:]
        self.assertEqual([r[0] for r in rows_after_run1], ['1', '3'])

        # 第二次运行（新实例，模拟重新点「开始执行」）：网络恢复，复用同一CSV
        session2 = FakeSession({
            0: make_page(1, 1, has_more=True),
            1: make_page(2, 1, has_more=True),
            2: make_page(3, 1, has_more=False),
        })
        spider2 = self.build_spider(session2, top_num=3)
        spider2.result_file = result_file
        spider2.get_user_post()

        # 只补采了失败的第2页，第1、3页未重复请求
        self.assertEqual(session2.calls, [1])
        rows = self.read_rows(result_file)
        data_rows = rows[1:]
        # 第一页不重复追加：全文件第1页仍只有1行，总计3行无重复
        self.assertEqual(len(data_rows), 3)
        self.assertEqual([r[0] for r in data_rows].count('1'), 1)
        self.assertEqual(sorted(r[0] for r in data_rows), ['1', '2', '3'])
        # 表头也只有一行
        self.assertEqual(sum(1 for r in rows if r[0] == '页码'), 1)
        # 进度文件：失败页已清除
        with open(spider2.progress_file, encoding='utf8') as f:
            record = json.load(f)[SEC_UID]
        self.assertEqual(record['success_pages'], [1, 3, 2])
        self.assertEqual(record['failed_pages'], {})


class TestDownloadSwitch(UserPostTestBase):
    def test_download_off_makes_no_download_call(self):
        """下载开关关闭时不发起任何下载请求"""
        session = FakeSession({0: make_page(1, 1, has_more=False)})
        spider = self.build_spider(session, top_num=1, down_tag=False)
        with mock.patch.object(spider, 'down_dy_video') as mock_down:
            spider.get_user_post()
        mock_down.assert_not_called()

    def test_download_on_calls_download(self):
        """下载开关打开时按作品发起下载（对照组）"""
        session = FakeSession({0: make_page(1, 1, has_more=False)})
        spider = self.build_spider(session, top_num=1, down_tag=True)
        with mock.patch.object(spider, 'down_dy_video') as mock_down:
            spider.get_user_post()
        self.assertEqual(mock_down.call_count, 1)
        # 去水印：playwm -> play
        self.assertIn('/play/', mock_down.call_args[0][1])


if __name__ == '__main__':
    unittest.main()
