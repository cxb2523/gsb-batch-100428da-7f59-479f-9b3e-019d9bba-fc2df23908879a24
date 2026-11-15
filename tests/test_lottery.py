# -*- coding: utf-8 -*-
import pytest
import requests

from TaiwanLottery import CrawlStatus, LotteryResult, TaiwanLotteryCrawler
from TaiwanLottery import utils


class FakeResponse:
    def __init__(self, payload=None, status_code=200, json_error=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def make_raw_row(number_key, numbers, period=112000052, lottery_date='2023-06-29T00:00:00'):
    return {'period': period, 'lotteryDate': lottery_date, number_key: list(numbers)}


def make_payload(result_key, rows):
    return {'content': {'totalSize': len(rows), result_key: rows}}


# (公開方法名, API 結果鍵, 數字欄位名, 一筆開獎號碼)
METHOD_SPECS = [
    ('super_lotto', 'superLotto638Res', 'drawNumberSize', [1, 8, 26, 27, 29, 36, 2]),
    ('lotto649', 'lotto649Res', 'drawNumberSize', [6, 22, 26, 29, 32, 43, 38]),
    ('daily_cash', 'daily539Res', 'drawNumberSize', [3, 11, 20, 30, 36]),
    ('lotto1224', 'lotto1224Res', 'drawNumberSize', [1, 2, 5, 6, 7, 10, 12, 14, 15, 16, 20, 24]),
    ('lotto3d', 'lotto3DHistoryRes', 'drawNumberAppear', [7, 2, 7]),
    ('lotto4d', 'lotto4DHistoryRes', 'drawNumberAppear', [1, 1, 6, 1]),
    ('lotto38m6', 'm638Res', 'drawNumberSize', [4, 22, 23, 27, 30, 34]),
    ('lotto49m6', 'm649Res', 'drawNumberSize', [1, 3, 22, 25, 34, 44]),
    ('lotto39m5', 'm539Res', 'drawNumberSize', [3, 13, 19, 21, 35]),
]


@pytest.mark.parametrize('method_name,result_key,number_key,numbers', METHOD_SPECS)
def test_all_methods_success(monkeypatch, method_name, result_key, number_key, numbers):
    # Given the official API returns one valid record for every lottery method
    payload = make_payload(result_key, [make_raw_row(number_key, numbers)])
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload))

    lottery = TaiwanLotteryCrawler()

    # When each public method is called with a fixed year-month
    outcome = getattr(lottery, method_name)(['2023', '06'])

    # Then it returns a LotteryResult marked successful with the parsed record
    assert isinstance(outcome, LotteryResult)
    assert outcome.status == CrawlStatus.SUCCESS
    assert outcome.error_message == ''
    assert outcome.request_year_month == ['2023', '06']
    assert len(outcome.records) == 1
    assert outcome.records[0]['期別'] == 112000052
    assert outcome.records[0]['開獎日期'] == '2023-06-29T00:00:00'


def test_super_lotto_success_fields_and_types(monkeypatch):
    # Given the 威力彩 API returns two well-formed records
    rows = [
        make_raw_row('drawNumberSize', [1, 8, 26, 27, 29, 36, 2], 112000052, '2023-06-29T00:00:00'),
        make_raw_row('drawNumberSize', [1, 15, 16, 17, 24, 35, 6], 112000051, '2023-06-26T00:00:00')
    ]
    monkeypatch.setattr(
        'TaiwanLottery.requests.get',
        lambda url: FakeResponse(make_payload('superLotto638Res', rows))
    )

    lottery = TaiwanLotteryCrawler()

    # When crawling 威力彩 2023-06
    outcome = lottery.super_lotto(['2023', '06'])

    # Then field names and types match the original list-based output exactly
    assert outcome.status == CrawlStatus.SUCCESS
    assert outcome.lottery_name == '威力彩'
    assert outcome.records == [
        {'期別': 112000052, '開獎日期': '2023-06-29T00:00:00', '第一區': [1, 8, 26, 27, 29, 36], '第二區': 2},
        {'期別': 112000051, '開獎日期': '2023-06-26T00:00:00', '第一區': [1, 15, 16, 17, 24, 35], '第二區': 6}
    ]


def test_non_json_response_is_request_failure(monkeypatch):
    # Given the server answers 200 but the body is not valid JSON
    monkeypatch.setattr(
        'TaiwanLottery.requests.get',
        lambda url: FakeResponse(json_error=ValueError('Expecting value: line 1 column 1'))
    )

    lottery = TaiwanLotteryCrawler()

    # When crawling
    outcome = lottery.super_lotto(['2023', '06'])

    # Then the outcome is a request failure with the JSON parsing context
    assert outcome.status == CrawlStatus.REQUEST_FAILED
    assert outcome.records == []
    assert 'JSON' in outcome.error_message


def test_non_2xx_response_is_request_failure(monkeypatch):
    # Given the server answers with HTTP 503
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload={}, status_code=503))

    lottery = TaiwanLotteryCrawler()

    # When crawling
    outcome = lottery.lotto649(['2023', '06'])

    # Then the outcome is a request failure carrying the HTTP status code
    assert outcome.status == CrawlStatus.REQUEST_FAILED
    assert outcome.records == []
    assert '503' in outcome.error_message


def test_network_error_is_request_failure(monkeypatch):
    # Given requests.get raises a network-level exception (e.g. timeout)
    def fake_get(url):
        raise requests.exceptions.ConnectionError('Connection timed out')

    monkeypatch.setattr('TaiwanLottery.requests.get', fake_get)

    lottery = TaiwanLotteryCrawler()

    # When crawling
    outcome = lottery.daily_cash(['2023', '06'])

    # Then the outcome is a request failure rather than a raised exception
    assert outcome.status == CrawlStatus.REQUEST_FAILED
    assert outcome.records == []
    assert '網路請求異常' in outcome.error_message


def test_missing_content_key_is_structure_error(monkeypatch):
    # Given the JSON parses but has no content key
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload={'foo': 'bar'}))

    lottery = TaiwanLotteryCrawler()

    # When crawling
    outcome = lottery.super_lotto(['2023', '06'])

    # Then the outcome is a structure error naming the missing key
    assert outcome.status == CrawlStatus.STRUCTURE_ERROR
    assert outcome.records == []
    assert 'content' in outcome.error_message


def test_missing_result_key_is_structure_error(monkeypatch):
    # Given content exists but the lottery-specific result key is absent
    payload = {'content': {'totalSize': 0, 'wrongKey': []}}
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload))

    lottery = TaiwanLotteryCrawler()

    # When crawling 大樂透
    outcome = lottery.lotto649(['2023', '06'])

    # Then the outcome is a structure error naming the missing result key
    assert outcome.status == CrawlStatus.STRUCTURE_ERROR
    assert 'lotto649Res' in outcome.error_message


def test_content_not_object_is_structure_error(monkeypatch):
    # Given content parses but is not a JSON object
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload={'content': []}))

    outcome = TaiwanLotteryCrawler().super_lotto(['2023', '06'])

    assert outcome.status == CrawlStatus.STRUCTURE_ERROR
    assert 'content' in outcome.error_message


def test_missing_totalsize_is_structure_error(monkeypatch):
    # Given content lacks the totalSize key
    payload = {'content': {'superLotto638Res': []}}
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload))

    outcome = TaiwanLotteryCrawler().super_lotto(['2023', '06'])

    assert outcome.status == CrawlStatus.STRUCTURE_ERROR
    assert 'totalSize' in outcome.error_message


def test_result_value_not_list_is_structure_error(monkeypatch):
    # Given the result key exists but does not hold an array
    payload = {'content': {'totalSize': 0, 'superLotto638Res': None}}
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload))

    outcome = TaiwanLotteryCrawler().super_lotto(['2023', '06'])

    assert outcome.status == CrawlStatus.STRUCTURE_ERROR
    assert '陣列' in outcome.error_message


def test_totalsize_mismatch_is_structure_error(monkeypatch):
    # Given totalSize disagrees with the actual array length
    rows = [make_raw_row('drawNumberSize', [1, 2, 3, 4, 5, 6, 7])]
    payload = {'content': {'totalSize': 2, 'superLotto638Res': rows}}
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload))

    lottery = TaiwanLotteryCrawler()

    # When crawling
    outcome = lottery.super_lotto(['2023', '06'])

    # Then the outcome is a structure error describing the mismatch
    assert outcome.status == CrawlStatus.STRUCTURE_ERROR
    assert 'totalSize' in outcome.error_message


def test_no_data_month_returns_no_data_status(monkeypatch):
    # Given the API reports zero records for the requested month
    payload = {'content': {'totalSize': 0, 'superLotto638Res': []}}
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload))

    lottery = TaiwanLotteryCrawler()

    # When crawling a month without draws
    outcome = lottery.super_lotto(['2023', '06'])

    # Then the outcome explicitly says no data instead of returning an empty list
    assert outcome.status == CrawlStatus.NO_DATA
    assert outcome.records == []
    assert outcome.error_message == ''


def test_structure_error_keeps_already_parsed_records(monkeypatch):
    # Given the second row is malformed while the first one is valid
    rows = [
        make_raw_row('drawNumberSize', [1, 8, 26, 27, 29, 36, 2], 112000052),
        {'period': 112000051, 'lotteryDate': '2023-06-26T00:00:00'}
    ]
    payload = {'content': {'totalSize': 2, 'superLotto638Res': rows}}
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload))

    lottery = TaiwanLotteryCrawler()

    # When crawling
    outcome = lottery.super_lotto(['2023', '06'])

    # Then the structure error still carries the record parsed before the failure
    assert outcome.status == CrawlStatus.STRUCTURE_ERROR
    assert len(outcome.records) == 1
    assert outcome.records[0]['期別'] == 112000052
    assert outcome.error_message != ''


def test_keyboard_interrupt_is_not_swallowed(monkeypatch):
    # Given requests.get is interrupted by a KeyboardInterrupt
    def fake_get(url):
        raise KeyboardInterrupt

    monkeypatch.setattr('TaiwanLottery.requests.get', fake_get)

    lottery = TaiwanLotteryCrawler()

    # When crawling, the non-Exception signal must propagate instead of being caught
    with pytest.raises(KeyboardInterrupt):
        lottery.super_lotto(['2023', '06'])


def test_default_arguments_remain_supported(monkeypatch):
    # Given a call without explicit year-month uses the current one
    payload = {'content': {'totalSize': 0, 'superLotto638Res': []}}
    monkeypatch.setattr('TaiwanLottery.requests.get', lambda url: FakeResponse(payload))

    lottery = TaiwanLotteryCrawler()

    # When calling with the default arguments
    outcome = lottery.super_lotto()

    # Then the request year-month reflects the current year and month
    assert outcome.request_year_month == [utils.get_current_year(), utils.get_current_month()]
    assert outcome.status == CrawlStatus.NO_DATA
