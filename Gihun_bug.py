# -*- coding: utf-8 -*-
"""
鉅亨網 (cnyes) 新聞採集腳本（重構版）

改造重點：
1. 起止時間戳不再寫死，可由函式參數 / 命令列傳入。
2. 依固定步長 WINDOW_STEP (= 2592000 + 86400 秒，約一個月重疊一天) 逐窗向前回溯，
   直到視窗邊界覆蓋到起始時間 (oldest_at) 為止。
3. 單一時間窗的列表請求失敗 / JSON 不合法 / 缺少 items、data 時，記錄原因並跳過該窗，
   先前視窗已成功取得的新聞繼續保留。
4. 單篇正文請求失敗或缺少 h1 / time / div._1UuP 節點時，跳過該篇，其餘新聞繼續處理。
5. 網路請求與頁面解析皆可注入 fetcher 取代，測試時可用本地 JSON / HTML 與可控失敗響應，
   不依賴真實網站。
6. 成功解析的新寫出 Final.xlsx（欄位與順序：標題、時間、內容），
   另產出 gihun_report.json，逐窗記錄成敗與原因，區分「該窗沒有新聞」與「該窗抓取失敗」。
"""

import argparse
import datetime
import json
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# 常數：保留原腳本的列表接口位址、分頁方式與回溯步長
# ---------------------------------------------------------------------------
LIST_URL = 'https://news.cnyes.com/api/v3/news/category/tw_stock'
ARTICLE_URL = 'https://news.cnyes.com/news/id/{news_id}?exp=a'

LIMIT = 30              # 每頁筆數，與原腳本一致
MAX_PAGE = 199          # 原腳本 for i in range(1, 200) 的上限保護
WINDOW_STEP = 2592000 + 86400   # 每次回溯一個月又一天（視窗間重疊一天）

# 與原腳本相同的預設時間範圍（直接執行本檔時使用，皆可由參數覆蓋）
DEFAULT_START_AT = 1567007999   # 2019/08/29 左右，最新一個視窗的 endAt 邊界
DEFAULT_OLDEST_AT = 1502207999  # 原腳本 while (start > 1502207999) 的停止門檻

DEFAULT_EXCEL_PATH = 'Final.xlsx'
DEFAULT_REPORT_PATH = 'gihun_report.json'


# ---------------------------------------------------------------------------
# 可注入的網路請求函式
# fetcher 合約：傳入 URL 字串，回傳帶有 .text 屬性的物件；失敗時拋出例外。
# ---------------------------------------------------------------------------
def default_http_get(url, timeout=30):
    """預設 fetcher：使用 requests 發送 GET 請求，HTTP 錯誤狀態碼會拋出例外。"""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response


def build_list_url(window_start_at, window_end_at, page):
    """組出與原腳本完全相同格式的列表 API 位址（startAt / endAt / limit=30 / page）。"""
    return (
        LIST_URL
        + '?startAt=' + str(window_start_at)
        + '&endAt=' + str(window_end_at)
        + '&limit=' + str(LIMIT)
        + '&page={}'.format(page)
    )


def build_article_url(news_id):
    return ARTICLE_URL.format(news_id=news_id)


# ---------------------------------------------------------------------------
# 列表 JSON 解析（獨立成純函式，方便用本地構造的 JSON 測試）
# ---------------------------------------------------------------------------
def parse_list_payload(text):
    """
    解析列表 API 回應文本，回傳 (items, next_page_url)。

    下列情況拋出 ValueError，交由呼叫端記錄並跳過該時間窗：
    - 文本不是合法 JSON
    - JSON 中缺少 items 或 items.data
    """
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('列表回應不是合法 JSON: {}'.format(exc))

    if not isinstance(payload, dict) or 'items' not in payload:
        raise ValueError("列表回應缺少 items 節點")

    items = payload['items']
    if not isinstance(items, dict) or 'data' not in items:
        raise ValueError("列表回應缺少 items.data 節點")

    return items['data'], items.get('next_page_url')


def fetch_window_news_ids(window_start_at, window_end_at,
                          fetcher=default_http_get, sleep_seconds=0):
    """
    抓取單一時間窗內、帶有 summary 的新聞 id。

    回傳 (news_ids, error)：
    - 成功（含一筆新聞都沒有）：news_ids 為依序收集到的 id 清單，error 為 None。
    - 任一頁請求失敗 / JSON 不合法 / 缺少 items、data：立即停止分頁，
      回傳 (失敗前已收集的 ids, 失敗原因字串)，由呼叫端將該窗標記為 failed。
    """
    news_ids = []
    for page in range(1, MAX_PAGE + 1):
        url = build_list_url(window_start_at, window_end_at, page)
        try:
            response = fetcher(url)
            data, next_page_url = parse_list_payload(response.text)
        except Exception as exc:  # 請求例外或解析例外皆視為該窗抓取失敗
            return news_ids, '{}: {}'.format(type(exc).__name__, exc)

        if not data:
            break  # 這一頁沒有資料，正常結束分頁

        for item in data:
            # 沿用原規則：只保留有 summary 的新聞；異常資料列略過
            try:
                if item.get('summary'):
                    news_ids.append(item['newsId'])
            except (AttributeError, TypeError, KeyError):
                continue

        if not next_page_url:
            break  # 沒有下一頁，正常結束分頁

        if sleep_seconds:
            time.sleep(sleep_seconds)

    return news_ids, None


# ---------------------------------------------------------------------------
# 正文 HTML 解析（獨立成純函式，方便用本地構造的 HTML 測試）
# ---------------------------------------------------------------------------
def parse_article_html(html_text):
    """
    從正文頁 HTML 取出 (標題, 時間, 內容)。

    沿用原腳本欄位規則：h1 取標題、time 取時間、class 為 _1UuP 的 div 取內容。
    任一節點缺少或文字為空時拋出 ValueError，由呼叫端跳過該篇。
    """
    soup = BeautifulSoup(html_text, 'html.parser')

    h1 = soup.find('h1')
    time_node = soup.find('time')
    content_node = soup.find('div', {'class': '_1UuP'})

    missing = []
    if h1 is None or not h1.text.strip():
        missing.append('h1 標題')
    if time_node is None or not time_node.text.strip():
        missing.append('time 時間')
    if content_node is None or not content_node.text.strip():
        missing.append('div._1UuP 內容')

    if missing:
        raise ValueError('正文缺少必要節點: {}'.format('、'.join(missing)))

    return h1.text, time_node.text, content_node.text


def fetch_article(news_id, fetcher=default_http_get):
    """
    抓取並解析單篇正文，成功回傳 (標題, 時間, 內容)。
    請求失敗或解析失敗時拋出例外（訊息即失敗原因），由呼叫端記錄並跳過該篇。
    """
    url = build_article_url(news_id)
    try:
        response = fetcher(url)
    except Exception as exc:
        raise RuntimeError('正文請求失敗: {}: {}'.format(type(exc).__name__, exc))
    return parse_article_html(response.text)


# ---------------------------------------------------------------------------
# 時間窗生成
# ---------------------------------------------------------------------------
def iter_windows(start_at, oldest_at, step=WINDOW_STEP):
    """
    由新到舊產出 (window_start_at, window_end_at) 時間窗。

    第一個視窗的 endAt 即傳入的 start_at；每個視窗向前回溯 step 秒
    （= 2592000 + 86400，與原腳本相同），直到視窗 endAt 不再晚於 oldest_at。
    """
    window_end = start_at
    while window_end > oldest_at:
        window_start = window_end - step
        yield window_start, window_end
        window_end = window_start


def _iso_utc(timestamp):
    try:
        return datetime.datetime.fromtimestamp(
            timestamp, datetime.timezone.utc
        ).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (OverflowError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 主流程：逐窗回溯、逐篇解析、容錯保存
# ---------------------------------------------------------------------------
def collect_news(start_at, oldest_at,
                 fetcher=default_http_get,
                 sleep_seconds=0,
                 excel_path=DEFAULT_EXCEL_PATH,
                 report_path=DEFAULT_REPORT_PATH):
    """
    依時間窗連續回溯採集，任一窗或任一篇失敗都不影響已成功的內容。

    回傳 (rows, report)：
    - rows：成功解析的新聞 list[dict]，鍵為 標題 / 時間 / 內容
    - report：可寫出 gihun_report.json 的結構化結局清單
    """
    rows = []
    seen_ids = set()
    window_reports = []

    total_ids_found = 0
    total_articles_failed = 0

    for index, (window_start, window_end) in enumerate(
            iter_windows(start_at, oldest_at), start=1):
        entry = {
            'window_index': index,
            'start_at': window_start,
            'end_at': window_end,
            'start_at_iso': _iso_utc(window_start),
            'end_at_iso': _iso_utc(window_end),
            'list_success': False,
            'status': 'failed',          # ok_with_news / ok_empty / failed
            'ids_found': 0,
            'new_ids': 0,
            'articles_success': 0,
            'articles_failed': 0,
            'article_failures': [],
            'failure_reason': None,
        }

        window_ids, list_error = fetch_window_news_ids(
            window_start, window_end, fetcher=fetcher,
            sleep_seconds=sleep_seconds,
        )
        entry['ids_found'] = len(window_ids)
        total_ids_found += len(window_ids)

        if list_error is not None:
            # 列表抓取失敗：跳過整個視窗，先前視窗的成果不受影響
            entry['list_success'] = False
            entry['status'] = 'failed'
            entry['failure_reason'] = list_error
            window_reports.append(entry)
            continue

        entry['list_success'] = True

        # 視窗間重疊一天可能出現重複 id，全域去重
        new_ids = [nid for nid in window_ids if nid not in seen_ids]
        seen_ids.update(new_ids)
        entry['new_ids'] = len(new_ids)

        if not new_ids:
            # 合法回應但沒有任何符合條件的新聞：與「抓取失敗」明確區分
            entry['status'] = 'ok_empty'
            window_reports.append(entry)
            continue

        entry['status'] = 'ok_with_news'

        for news_id in new_ids:
            try:
                title, date_text, content = fetch_article(news_id, fetcher=fetcher)
            except Exception as exc:
                entry['articles_failed'] += 1
                total_articles_failed += 1
                entry['article_failures'].append({
                    'news_id': news_id,
                    'reason': '{}: {}'.format(type(exc).__name__, exc),
                })
                continue  # 單篇失敗只跳過該篇

            rows.append({
                '標題': title,
                '時間': date_text,
                '內容': content,
            })
            entry['articles_success'] += 1

        window_reports.append(entry)

    report = {
        'generated_at': datetime.datetime.now(datetime.timezone.utc)
        .strftime('%Y-%m-%dT%H:%M:%SZ'),
        'params': {
            'start_at': start_at,
            'oldest_at': oldest_at,
            'window_step_seconds': WINDOW_STEP,
            'limit': LIMIT,
            'max_page': MAX_PAGE,
            'list_url': LIST_URL,
        },
        'total_windows': len(window_reports),
        'windows_succeeded': sum(1 for w in window_reports if w['list_success']),
        'windows_failed': sum(1 for w in window_reports if not w['list_success']),
        'windows_empty': sum(1 for w in window_reports if w['status'] == 'ok_empty'),
        'total_ids_found': total_ids_found,
        'unique_news_ids': len(seen_ids),
        'total_articles_success': len(rows),
        'total_articles_failed': total_articles_failed,
        'windows': window_reports,
    }

    if excel_path:
        store(rows, excel_path)
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

    return rows, report


# ---------------------------------------------------------------------------
# 輸出：保留原函式名稱與欄位順序
# ---------------------------------------------------------------------------
def store(rows, path=DEFAULT_EXCEL_PATH):
    """用 pandas 寫出 Excel，欄名與順序固定為 標題、時間、內容，只含成功解析的新聞。"""
    pd_store = pd.DataFrame(rows, columns=['標題', '時間', '內容'])
    pd_store.to_excel(path, index=False)


def get_newsid(start, old, fetcher=default_http_get, sleep_seconds=0):
    """
    相容舊介面：回傳回溯範圍內所有帶 summary 的新聞 id（已跨視窗去重）。
    start / old 即為可傳入的起訖時間戳。
    """
    news_ids = []
    seen = set()
    for window_start, window_end in iter_windows(start, old):
        window_ids, error = fetch_window_news_ids(
            window_start, window_end, fetcher=fetcher,
            sleep_seconds=sleep_seconds,
        )
        if error is not None:
            continue  # 該窗失敗就跳過，繼續下一個窗
        for news_id in window_ids:
            if news_id not in seen:
                seen.add(news_id)
                news_ids.append(news_id)
    return news_ids


def get_content(newsid, fetcher=default_http_get, sleep_seconds=0):
    """
    相容舊介面：逐篇取正文，回傳 (title, date_data, content)。
    單篇失敗只跳過該篇，不影響其他成功的新聞。
    """
    title, date_data, content = [], [], []
    for news_id in newsid:
        try:
            t, d, c = fetch_article(news_id, fetcher=fetcher)
        except Exception:
            continue
        title.append(t)
        date_data.append(d)
        content.append(c)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return title, date_data, content


def main(argv=None):
    parser = argparse.ArgumentParser(description='鉅亨網台股新聞回溯採集腳本')
    parser.add_argument('--start', type=int, default=DEFAULT_START_AT,
                        help='最新時間窗的 endAt 時間戳（預設 %(default)s）')
    parser.add_argument('--oldest', type=int, default=DEFAULT_OLDEST_AT,
                        help='回溯停止門檻時間戳（預設 %(default)s）')
    parser.add_argument('--excel', default=DEFAULT_EXCEL_PATH,
                        help='Excel 輸出路徑（預設 %(default)s）')
    parser.add_argument('--report', default=DEFAULT_REPORT_PATH,
                        help='結局清單 JSON 輸出路徑（預設 %(default)s）')
    parser.add_argument('--sleep', type=float, default=0,
                        help='每次請求之間的等待秒數（預設 0）')
    args = parser.parse_args(argv)

    rows, report = collect_news(
        start_at=args.start,
        oldest_at=args.oldest,
        fetcher=default_http_get,
        sleep_seconds=args.sleep,
        excel_path=args.excel,
        report_path=args.report,
    )

    print('時間窗數: {total_windows}（成功 {windows_succeeded}、'
          '空窗 {windows_empty}、失敗 {windows_failed}）'.format(**report))
    print('成功入庫新聞: {} 篇；失敗: {} 篇'.format(
        report['total_articles_success'], report['total_articles_failed']))
    print('Excel 輸出: {}；結局清單: {}'.format(args.excel, args.report))


if __name__ == '__main__':
    main()
