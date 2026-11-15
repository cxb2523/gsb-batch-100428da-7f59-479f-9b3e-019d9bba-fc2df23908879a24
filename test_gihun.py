# -*- coding: utf-8 -*-
"""
Gihun_bug.py 的本地單元測試：

以本地構造的列表 JSON、正文 HTML 與可控失敗響應，驗證四種必要情形：
  1. 正常時間窗（含分頁、summary 過濾、正文解析、Excel / 報告輸出）
  2. 空窗（合法回應但沒有新聞，須與抓取失敗區分）
  3. 列表請求失敗（請求例外 / 非合法 JSON / 缺少 items、data，跳窗後續跑、舊成果保留）
  4. 單篇正文解析失敗（請求失敗 / 缺節點，只跳過該篇）

全程不發出任何真實網路請求。
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import Gihun_bug as g


STEP = g.WINDOW_STEP


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        """模擬真實 requests.Response：正常回應不拋例外。"""
        return None


class FakeFetcher:
    """路由表 fetcher：URL -> 回應文本（或要拋出的例外）。"""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        route = self.routes[url]
        if isinstance(route, Exception):
            raise route
        return FakeResponse(route)


def list_payload(items, next_page_url=None):
    return json.dumps({'items': {'data': items, 'next_page_url': next_page_url}})


def article_html(title='標題', time_text='2019-08-01 10:00', content='內文'):
    return (
        '<html><head></head><body>'
        '<h1>{t}</h1><time>{d}</time>'
        '<div class="_1UuP">{c}</div>'
        '</body></html>'
    ).format(t=title, d=time_text, c=content)


def list_url(window_start, window_end, page):
    return g.build_list_url(window_start, window_end, page)


def article_url(news_id):
    return g.build_article_url(news_id)


class ParseUnitTests(unittest.TestCase):
    """純函式層級的解析容錯測試。"""

    def test_parse_list_payload_valid(self):
        data, nxt = g.parse_list_payload(list_payload([{'newsId': 1}], '/next'))
        self.assertEqual(data, [{'newsId': 1}])
        self.assertEqual(nxt, '/next')

    def test_parse_list_payload_bad_json_and_missing_nodes(self):
        with self.assertRaises(ValueError):
            g.parse_list_payload('not a json')
        with self.assertRaises(ValueError):
            g.parse_list_payload(json.dumps({'no_items': {}}))
        with self.assertRaises(ValueError):
            g.parse_list_payload(json.dumps({'items': {'no_data': []}}))

    def test_parse_article_html_missing_nodes(self):
        good = g.parse_article_html(article_html())
        self.assertEqual(good, ('標題', '2019-08-01 10:00', '內文'))

        with self.assertRaises(ValueError):
            g.parse_article_html('<html><body><time>t</time>'
                                 '<div class="_1UuP">c</div></body></html>')
        with self.assertRaises(ValueError):
            g.parse_article_html('<html><body><h1>t</h1>'
                                 '<div class="_1UuP">c</div></body></html>')
        with self.assertRaises(ValueError):
            g.parse_article_html('<html><body><h1>t</h1>'
                                 '<time>d</time></body></html>')
        with self.assertRaises(ValueError):
            g.parse_article_html('<h1></h1><time>d</time>'
                                 '<div class="_1UuP">c</div>')

    def test_iter_windows_step_and_stop(self):
        start, oldest = 10_000_000, 10_000_000 - 2 * STEP
        windows = list(g.iter_windows(start, oldest))
        self.assertEqual(windows, [
            (start - STEP, start),
            (start - 2 * STEP, start - STEP),
        ])
        # 邊界相等即停止，不會多跑一個視窗
        self.assertEqual(list(g.iter_windows(start, start)), [])


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.excel_path = os.path.join(self.tmp.name, 'Final.xlsx')
        self.report_path = os.path.join(self.tmp.name, 'gihun_report.json')

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, start_at, oldest_at, routes):
        fetcher = FakeFetcher(routes)
        rows, report = g.collect_news(
            start_at=start_at,
            oldest_at=oldest_at,
            fetcher=fetcher,
            excel_path=self.excel_path,
            report_path=self.report_path,
        )
        with open(self.report_path, encoding='utf-8') as fh:
            saved_report = json.load(fh)
        return fetcher, rows, report, saved_report

    # ------------------------------------------------------------------
    # 情形 1：正常時間窗（兩頁分頁、過濾空 summary、正文成功、輸出正確）
    # ------------------------------------------------------------------
    def test_normal_window_with_pagination(self):
        start = 10_000_000
        oldest = start - STEP
        w_start, w_end = start - STEP, start

        page1_items = [
            {'newsId': 101, 'summary': '有摘要'},
            {'newsId': 102, 'summary': ''},          # 應被過濾
            {'newsId': 103, 'summary': '也有摘要'},
        ]
        page2_items = [
            {'newsId': 104, 'summary': '第二頁'},
            {'newsId': 105},                          # 無 summary 鍵，應被過濾
        ]
        routes = {
            list_url(w_start, w_end, 1): list_payload(page1_items, '/next-page'),
            list_url(w_start, w_end, 2): list_payload(page2_items, None),
            article_url(101): article_html('頭條一', '2019-08-01 09:00', '內文一'),
            article_url(103): article_html('頭條三', '2019-08-02 09:00', '內文三'),
            article_url(104): article_html('頭條四', '2019-08-03 09:00', '內文四'),
        }

        fetcher, rows, report, saved_report = self._run(start, oldest, routes)

        # 三個帶 summary 的 id，分頁走到第 2 頁停止
        self.assertEqual(report['total_ids_found'], 3)
        self.assertEqual(len(fetcher.calls), 2 + 3)  # 2 次列表 + 3 次正文

        self.assertEqual(len(rows), 3)
        self.assertEqual([r['標題'] for r in rows], ['頭條一', '頭條三', '頭條四'])
        self.assertEqual(rows[0], {
            '標題': '頭條一', '時間': '2019-08-01 09:00', '內容': '內文一',
        })

        window = report['windows'][0]
        self.assertTrue(window['list_success'])
        self.assertEqual(window['status'], 'ok_with_news')
        self.assertEqual(window['ids_found'], 3)
        self.assertEqual(window['articles_success'], 3)
        self.assertEqual(window['articles_failed'], 0)
        self.assertIsNone(window['failure_reason'])
        self.assertEqual(report['total_articles_success'], 3)
        self.assertEqual(report['total_articles_failed'], 0)

        # 報告檔內容與回傳值一致
        self.assertEqual(saved_report['windows'][0]['status'], 'ok_with_news')
        self.assertEqual(saved_report['total_articles_success'], 3)

        # Excel 欄名與順序固定為 標題、時間、內容
        df = pd.read_excel(self.excel_path)
        self.assertEqual(list(df.columns), ['標題', '時間', '內容'])
        self.assertEqual(len(df), 3)
        self.assertEqual(df['標題'].tolist(), ['頭條一', '頭條三', '頭條四'])

    # ------------------------------------------------------------------
    # 情形 2：空窗 —— 合法 JSON、data 為空，須與「抓取失敗」區分
    # ------------------------------------------------------------------
    def test_empty_window_is_distinguished_from_failure(self):
        start = 10_000_000
        oldest = start - STEP
        w_start, w_end = start - STEP, start

        routes = {
            list_url(w_start, w_end, 1): list_payload([], None),
        }
        _, rows, report, _ = self._run(start, oldest, routes)

        self.assertEqual(rows, [])
        self.assertEqual(report['total_windows'], 1)
        self.assertEqual(report['windows_succeeded'], 1)
        self.assertEqual(report['windows_failed'], 0)
        self.assertEqual(report['windows_empty'], 1)

        window = report['windows'][0]
        self.assertTrue(window['list_success'])
        self.assertEqual(window['status'], 'ok_empty')
        self.assertEqual(window['ids_found'], 0)
        self.assertIsNone(window['failure_reason'])

        # 空窗也要照樣寫出只含表頭的 Excel
        df = pd.read_excel(self.excel_path)
        self.assertEqual(list(df.columns), ['標題', '時間', '內容'])
        self.assertEqual(len(df), 0)

    # ------------------------------------------------------------------
    # 情形 3：列表請求失敗 —— 跳過該窗繼續下一個窗，先前/後續成果都保留
    # ------------------------------------------------------------------
    def test_list_failures_skip_window_but_keep_going(self):
        start = 10_000_000
        oldest = start - 2 * STEP
        win1 = (start - STEP, start)          # 第一窗（較新）：失敗
        win2 = (start - 2 * STEP, start - STEP)  # 第二窗（較舊）：成功

        failure_variants = [
            ConnectionError('connection refused'),
            'this is not json',
            json.dumps({'no_items_here': {}}),
            json.dumps({'items': {'no_data': []}}),
        ]

        for bad_body in failure_variants:
            with self.subTest(bad_body=bad_body):
                routes = {
                    list_url(*win1, 1): bad_body,
                    list_url(*win2, 1): list_payload(
                        [{'newsId': 201, 'summary': 's'}], None),
                    article_url(201): article_html('倖存新聞', '2018-01-01', '內文'),
                }
                _, rows, report, saved_report = self._run(start, oldest, routes)

                self.assertEqual(report['total_windows'], 2)
                self.assertEqual(report['windows_failed'], 1)
                self.assertEqual(report['windows_succeeded'], 1)

                failed_window, ok_window = report['windows']
                self.assertFalse(failed_window['list_success'])
                self.assertEqual(failed_window['status'], 'failed')
                self.assertIsNotNone(failed_window['failure_reason'])
                self.assertTrue(ok_window['list_success'])
                self.assertEqual(ok_window['status'], 'ok_with_news')

                # 失敗窗之後的視窗仍成功，已取得的新聞沒有一起丟掉
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['標題'], '倖存新聞')
                self.assertEqual(report['total_articles_success'], 1)
                self.assertEqual(saved_report['windows'][0]['status'], 'failed')

    def test_list_failure_after_partial_pages_keeps_collected_ids_count(self):
        """第 1 頁成功、第 2 頁失敗：該窗記為 failed，但失敗前的 id 數仍可核對。"""
        start = 10_000_000
        oldest = start - STEP
        w_start, w_end = start - STEP, start

        routes = {
            list_url(w_start, w_end, 1): list_payload(
                [{'newsId': 301, 'summary': 's'}], '/next'),
            list_url(w_start, w_end, 2): TimeoutError('read timed out'),
        }
        _, rows, report, _ = self._run(start, oldest, routes)

        window = report['windows'][0]
        self.assertEqual(window['status'], 'failed')
        self.assertFalse(window['list_success'])
        self.assertEqual(window['ids_found'], 1)       # 失敗前已發現的 id 數
        self.assertIn('TimeoutError', window['failure_reason'])
        self.assertEqual(rows, [])                     # 失敗窗不寫入任何新聞

    # ------------------------------------------------------------------
    # 情形 4：單篇正文失敗 —— 只跳過該篇，其他新聞正常入庫
    # ------------------------------------------------------------------
    def test_article_failures_skip_only_that_article(self):
        start = 10_000_000
        oldest = start - STEP
        w_start, w_end = start - STEP, start

        routes = {
            list_url(w_start, w_end, 1): list_payload([
                {'newsId': 401, 'summary': 's1'},
                {'newsId': 402, 'summary': 's2'},
                {'newsId': 403, 'summary': 's3'},
            ], None),
            article_url(401): article_html('第一篇', '2019-08-01', '內文一'),
            article_url(402): ConnectionError('500 server error'),   # 請求失敗
            article_url(403): '<html><body><h1>缺正文</h1>'
                              '<time>2019-08-03</time></body></html>',  # 缺 _1UuP
        }
        _, rows, report, _ = self._run(start, oldest, routes)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['標題'], '第一篇')

        window = report['windows'][0]
        self.assertTrue(window['list_success'])
        self.assertEqual(window['status'], 'ok_with_news')
        self.assertEqual(window['ids_found'], 3)
        self.assertEqual(window['articles_success'], 1)
        self.assertEqual(window['articles_failed'], 2)
        self.assertEqual(report['total_articles_success'], 1)
        self.assertEqual(report['total_articles_failed'], 2)

        reasons = {f['news_id']: f['reason'] for f in window['article_failures']}
        self.assertIn('ConnectionError', reasons[402])
        self.assertIn('ValueError', reasons[403])
        self.assertIn('_1UuP', reasons[403])

        # 報告中的失敗原因摘要可序列化成 JSON
        json.dumps(report, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 跨視窗重疊造成的重複 id 應去重
    # ------------------------------------------------------------------
    def test_duplicate_ids_across_windows_are_deduplicated(self):
        start = 10_000_000
        oldest = start - 2 * STEP
        win1 = (start - STEP, start)
        win2 = (start - 2 * STEP, start - STEP)

        routes = {
            list_url(*win1, 1): list_payload(
                [{'newsId': 501, 'summary': 'a'}], None),
            list_url(*win2, 1): list_payload([
                {'newsId': 501, 'summary': 'a'},   # 與上一窗重複
                {'newsId': 502, 'summary': 'b'},
            ], None),
            article_url(501): article_html('重複篇', 't1', 'c1'),
            article_url(502): article_html('新的篇', 't2', 'c2'),
        }
        _, rows, report, _ = self._run(start, oldest, routes)

        self.assertEqual(report['unique_news_ids'], 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['標題'] for r in rows], ['重複篇', '新的篇'])
        self.assertEqual(report['windows'][1]['ids_found'], 2)
        self.assertEqual(report['windows'][1]['new_ids'], 1)

    # ------------------------------------------------------------------
    # 舊介面 get_newsid / get_content 仍可運作且同樣容錯
    # ------------------------------------------------------------------
    def test_legacy_interfaces_still_work(self):
        start = 10_000_000
        oldest = start - STEP
        w_start, w_end = start - STEP, start

        routes = {
            list_url(w_start, w_end, 1): list_payload([
                {'newsId': 601, 'summary': 's'},
                {'newsId': 602, 'summary': ''},
            ], None),
            article_url(601): article_html('舊介面', 'time', 'body'),
        }
        fetcher = FakeFetcher(routes)

        ids = g.get_newsid(start, oldest, fetcher=fetcher)
        self.assertEqual(ids, [601])

        titles, dates, contents = g.get_content(ids, fetcher=fetcher)
        self.assertEqual(titles, ['舊介面'])
        self.assertEqual(dates, ['time'])
        self.assertEqual(contents, ['body'])

    # ------------------------------------------------------------------
    # CLI 入口冒煙測試（patch 掉 requests，仍無真實網路請求）
    # ------------------------------------------------------------------
    def test_cli_main_smoke(self):
        start = 10_000_000
        oldest = start - STEP
        w_start, w_end = start - STEP, start
        routes = {
            list_url(w_start, w_end, 1): list_payload(
                [{'newsId': 701, 'summary': 's'}], None),
            article_url(701): article_html('CLI 篇', 't', 'c'),
        }

        def fake_requests_get(url, timeout=30):
            route = routes[url]
            if isinstance(route, Exception):
                raise route
            return FakeResponse(route)

        excel = os.path.join(self.tmp.name, 'cli.xlsx')
        report = os.path.join(self.tmp.name, 'cli_report.json')
        with patch.object(g.requests, 'get', side_effect=fake_requests_get):
            g.main(['--start', str(start), '--oldest', str(oldest),
                    '--excel', excel, '--report', report])

        self.assertTrue(os.path.exists(excel))
        with open(report, encoding='utf-8') as fh:
            saved = json.load(fh)
        self.assertEqual(saved['total_articles_success'], 1)
        self.assertEqual(saved['params']['start_at'], start)


if __name__ == '__main__':
    unittest.main(verbosity=2)
