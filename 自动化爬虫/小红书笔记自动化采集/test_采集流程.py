# -*- coding: utf-8 -*-
"""用本地假页面与假响应验证 小红书.py 的采集流程。

覆盖场景：
1. 正常卡片能写入并计数；
2. 监听超时与字段缺失分别归入对应失败原因，点击异常归入点击失败；
3. 重复 data-index 只算一次（不重复写入、不计成功，计为跳过）；
4. 失败达到上限时提前停止，汇总标明提前停止；
5. 单个位置失败不中断整轮采集，且失败后关闭已打开卡片。

运行：python test_采集流程.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('xhs', os.path.join(HERE, '小红书.py'))
xhs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xhs)


# ---------- 假页面与假响应 ----------

class FakeResponse:
    def __init__(self, body):
        self.body = body


class FakePacket:
    def __init__(self, body):
        self.response = FakeResponse(body)


def make_body(index):
    return {
        'items': [{
            'note_card': {
                'user': {'nickname': f'博主{index}'},
                'title': f'标题{index}',
                'desc': f'详情{index}',
                'interact_info': {'comment_count': 3, 'liked_count': 9},
            }
        }]
    }


class FakeImg:
    """卡片封面图，点击后打开卡片。"""
    def __init__(self, page, card):
        self.page = page
        self.card = card

    def click(self, by_js=False):
        if self.card.behavior == 'click_fail':
            raise RuntimeError('模拟点击异常（图层遮蔽等）')
        self.page.current_card = self.card
        self.page.open_count += 1


class FakeCard:
    def __init__(self, page, index, behavior):
        self.page = page
        self.index = index
        self.behavior = behavior  # ok / timeout / missing / click_fail

    def attr(self, name):
        return self.index if name == 'data-index' else None

    def ele(self, xpath):
        return FakeImg(self.page, self)


class FakeCloser:
    """卡片关闭按钮。"""
    def __init__(self, page):
        self.page = page

    def click(self):
        self.page.current_card = None
        self.page.close_count += 1


class FakeListen:
    def __init__(self, page):
        self.page = page

    def start(self, pattern):
        self.pattern = pattern

    def wait(self, count, timeout, fit_count):
        card = self.page.current_card
        assert card is not None, '未点开卡片却在等待响应'
        assert timeout == xhs.LISTEN_TIMEOUT
        if card.behavior == 'timeout':
            return False  # DrissionPage 超时返回 False
        if card.behavior == 'missing':
            return FakePacket({'data': {'desc': '只有详情，没有昵称和标题'}})
        return FakePacket(make_body(card.index))


class FakeWait:
    def load_start(self):
        pass


class FakeScroll:
    def down(self, pixels):
        pass


class FakePage:
    def __init__(self, cards_spec):
        self.current_card = None
        self.open_count = 0
        self.close_count = 0
        self.listen = FakeListen(self)
        self.wait = FakeWait()
        self.scroll = FakeScroll()
        self.cards = [FakeCard(self, idx, bhv) for idx, bhv in cards_spec]

    def get(self, url):
        self.url = url

    def eles(self, xpath):
        return list(self.cards)

    def ele(self, xpath, timeout=None):
        return FakeCloser(self)


class FakeRecorder:
    def __init__(self):
        self.rows = []

    def add_data(self, data):
        self.rows.append(data)

    def record(self):
        pass


# ---------- 测试辅助 ----------

def run_handler(cards_spec, max_count=20, fail_limit=5):
    page = FakePage(cards_spec)
    recorder = FakeRecorder()
    old = (xhs.create_xlsx, xhs.MAX_COUNT, xhs.FAIL_LIMIT)
    xhs.create_xlsx = lambda keyword: recorder
    xhs.MAX_COUNT = max_count
    xhs.FAIL_LIMIT = fail_limit
    try:
        summary = xhs.handler(page, '测试关键词')
    finally:
        xhs.create_xlsx, xhs.MAX_COUNT, xhs.FAIL_LIMIT = old
    return page, recorder, summary


def check(name, cond, detail=''):
    status = 'PASS' if cond else 'FAIL'
    print(f'[{status}] {name}' + (f' -- {detail}' if detail and not cond else ''))
    if not cond:
        raise AssertionError(f'{name}: {detail}')


# ---------- 测试用例 ----------

def test_normal_cards():
    spec = [(str(i), 'ok') for i in range(20)]
    page, recorder, s = run_handler(spec)
    check('正常卡片: 成功20条', s['success'] == 20, s)
    check('正常卡片: 无失败无跳过', s['failed'] == 0 and s['skipped'] == 0, s)
    check('正常卡片: 写入20行', len(recorder.rows) == 20, len(recorder.rows))
    check('正常卡片: 昵称标题写入正确',
          recorder.rows[0]['博主昵称'] == '博主0' and recorder.rows[0]['标题'] == '标题0',
          recorder.rows[0])
    check('正常卡片: 未提前停止', s['stopped_early'] is False, s)
    check('正常卡片: 每张卡片都关闭', page.close_count == 20, page.close_count)


def test_failure_reasons():
    spec = [('a', 'ok'), ('b', 'timeout'), ('c', 'missing'), ('d', 'click_fail'), ('e', 'ok')]
    page, recorder, s = run_handler(spec)
    check('失败分类: 成功2条', s['success'] == 2, s)
    check('失败分类: 失败3条', s['failed'] == 3, s)
    reasons = {idx: r for idx, r in s['failures']}
    check('失败分类: 监听超时归类正确', reasons.get('b') == xhs.REASON_TIMEOUT, s['failures'])
    check('失败分类: 字段缺失归类正确', reasons.get('c') == xhs.REASON_FIELD, s['failures'])
    check('失败分类: 点击失败归类正确', reasons.get('d') == xhs.REASON_CLICK, s['failures'])
    check('失败分类: 只写入成功的2行', len(recorder.rows) == 2, len(recorder.rows))
    check('失败分类: 单点失败不中断整轮(最后一个位置仍被处理)',
          any(idx == 'e' for idx, _ in [('e', None)]) and s['success'] == 2, s)
    # 点开过的卡片(成功/超时/字段缺失)都应被关闭；点击失败的未打开不需要关闭
    check('失败分类: 已打开卡片均被关闭', page.close_count == page.open_count == 4,
          f'open={page.open_count} close={page.close_count}')
    check('失败分类: 未达上限不提前停止', s['stopped_early'] is False, s)


def test_duplicate_data_index():
    spec = [('x', 'ok'), ('x', 'ok'), ('y', 'ok'), ('x', 'ok')]
    page, recorder, s = run_handler(spec)
    check('去重: 成功2条', s['success'] == 2, s)
    check('去重: 跳过2条', s['skipped'] == 2, s)
    check('去重: 只写入2行(重复位置不重复写入)', len(recorder.rows) == 2, len(recorder.rows))
    written = {(r['博主昵称'], r['标题']) for r in recorder.rows}
    check('去重: 写入内容为 x 和 y', written == {('博主x', '标题x'), ('博主y', '标题y')}, written)


def test_fail_limit_stops_early():
    spec = [(str(i), 'timeout') for i in range(10)]
    page, recorder, s = run_handler(spec, fail_limit=3)
    check('上限停止: 失败3条即停', s['failed'] == 3, s)
    check('上限停止: 标记提前停止', s['stopped_early'] is True, s)
    check('上限停止: 未采满不当成采满', s['success'] == 0 and s['success'] < 20, s)
    check('上限停止: 剩余位置未再处理(只打开3张卡)', page.open_count == 3, page.open_count)
    check('上限停止: 无写入', len(recorder.rows) == 0, len(recorder.rows))


def test_mixed_with_limit_not_reached():
    # 失败未达上限时，即使最后未采满也不应标记提前停止
    spec = [('a', 'ok'), ('b', 'missing'), ('c', 'ok')]
    page, recorder, s = run_handler(spec, fail_limit=5)
    check('未采满: 成功2失败1', s['success'] == 2 and s['failed'] == 1, s)
    check('未采满: 不标记提前停止', s['stopped_early'] is False, s)


if __name__ == '__main__':
    test_normal_cards()
    test_failure_reasons()
    test_duplicate_data_index()
    test_fail_limit_stops_early()
    test_mixed_with_limit_not_reached()
    print('\n全部测试通过')
