# -*- coding: utf-8 -*-
import datetime
import json
import logging

from TaiwanLottery import CrawlStatus, LotteryResult, utils


def test_convert_to_republic_era_month():
    # Given user wants to convert 2023-06
    # When user get the 2023-06 convert result
    republic_era_month_result = utils.convert_to_republic_era_month('2023-06')

    # Then the republic_era_month_result should be equal to [112, 6]
    assert republic_era_month_result == [112, 6]


def test_print_to_table_with_empty_result_object(capsys):
    # Given a no-data outcome with zero records
    outcome = LotteryResult('威力彩', ['2023', '06'], CrawlStatus.NO_DATA, [])

    # When printing the outcome, it must not raise on datas[0]
    utils.print_to_table('威力彩_2023_06', outcome)

    # Then the status and record count are printed
    captured = capsys.readouterr()
    assert CrawlStatus.NO_DATA in captured.out
    assert '記錄數: 0' in captured.out


def test_print_to_table_with_failed_result_object(capsys):
    # Given a request-failure outcome carrying a reason
    outcome = LotteryResult(
        '大樂透', ['2023', '06'], CrawlStatus.REQUEST_FAILED, [], 'HTTP 狀態碼 503，非 2xx 成功回應'
    )

    # When printing the outcome
    utils.print_to_table('大樂透_2023_06', outcome)

    # Then the failure reason is printed without raising
    captured = capsys.readouterr()
    assert CrawlStatus.REQUEST_FAILED in captured.out
    assert '503' in captured.out


def test_print_to_table_with_successful_result_object(capsys):
    # Given a successful outcome containing two-zone records
    records = [
        {'期別': 112000052, '開獎日期': '2023-06-29T00:00:00', '第一區': [1, 8, 26, 27, 29, 36], '第二區': 2}
    ]
    outcome = LotteryResult('威力彩', ['2023', '06'], CrawlStatus.SUCCESS, records)

    # When printing the outcome
    utils.print_to_table('威力彩_2023_06', outcome)

    # Then the record content is rendered
    captured = capsys.readouterr()
    assert CrawlStatus.SUCCESS in captured.out
    assert '記錄數: 1' in captured.out
    assert '112000052' in captured.out


def test_print_to_table_keeps_backward_compatible_list_input(capsys):
    # Given a plain list of records (the old calling convention)
    records = [
        {'期別': 112000064, '開獎日期': '2023-06-30T00:00:00', '獎號': [6, 22, 26, 29, 32, 43], '特別號': 38}
    ]

    # When printing the list directly
    utils.print_to_table('大樂透_2023_06', records)

    # Then it renders as before
    captured = capsys.readouterr()
    assert '112000064' in captured.out
    assert '特別號' in captured.out


def test_print_to_table_with_empty_list(capsys):
    # Given an empty plain list
    # When printing, it must not raise on datas[0]
    utils.print_to_table('空資料', [])

    captured = capsys.readouterr()
    assert '期別' in captured.out


def test_print_to_table_with_special_number_records(capsys):
    # Given records using 獎號 plus 特別號 columns
    records = [
        {'期別': 112000064, '開獎日期': '2023-06-30T00:00:00', '獎號': [6, 22, 26], '特別號': 38},
        {'期別': 112000063, '開獎日期': '2023-06-27T00:00:00', '獎號': [13, 24, 30], '特別號': 4}
    ]

    utils.print_to_table('大樂透_2023_06', records)

    captured = capsys.readouterr()
    assert '特別號' in captured.out
    assert '112000063' in captured.out


def test_print_to_table_with_plain_number_records(capsys):
    # Given records using only the 獎號 column (e.g. 今彩539)
    records = [
        {'期別': 112000155, '開獎日期': '2023-06-30T00:00:00', '獎號': [3, 11, 20, 30, 36]}
    ]

    utils.print_to_table('今彩539_2023_06', records)

    captured = capsys.readouterr()
    assert '112000155' in captured.out
    assert '特別號' not in captured.out


def test_output_to_json_round_trip(tmp_path):
    # Given a result object and an output path
    outcome = LotteryResult('威力彩', ['2023', '06'], CrawlStatus.SUCCESS, [
        {'期別': 1, '開獎日期': '2023-06-29T00:00:00', '第一區': [1, 2], '第二區': 3}
    ])
    target = tmp_path / 'result'

    # When writing JSON
    utils.output_to_json(str(target), outcome.records)

    # Then the file is created and contains the records
    data = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert data[0]['期別'] == 1


def test_output_to_json_permission_error_is_handled(caplog, tmp_path, monkeypatch):
    caplog.set_level(logging.ERROR)
    # Given writing to the target raises a permission error

    def broken_open(*args, **kwargs):
        raise PermissionError('denied')

    monkeypatch.setattr('builtins.open', broken_open)

    # When writing JSON, the error is swallowed and logged instead of raised
    utils.output_to_json(str(tmp_path / 'locked'), [])

    assert '存取權限異常' in caplog.text


def test_output_to_json_unknown_error_is_handled(caplog, tmp_path, monkeypatch):
    caplog.set_level(logging.ERROR)
    # Given writing to the target raises an unexpected error

    def broken_open(*args, **kwargs):
        raise OSError('unexpected')

    monkeypatch.setattr('builtins.open', broken_open)

    # When writing JSON, the unknown error is swallowed and logged
    utils.output_to_json(str(tmp_path / 'locked'), [])

    assert '未知的錯誤' in caplog.text


def test_month_diff_returns_republic_era_year_month():
    # Given a back-month offset far enough in the past
    # When computing the diff against the current month
    result = utils.month_diff(1)

    # Then a [year, month] pair is returned
    assert isinstance(result, list)
    assert len(result) == 2


def test_month_diff_invalid_input_logs_error(caplog):
    caplog.set_level(logging.ERROR)

    # Given an invalid (non-positive-distance) offset
    # When computing the diff, an error is logged but execution continues
    result = utils.month_diff(-1)

    assert '輸入格式錯誤' in caplog.text
    assert isinstance(result, list)


def test_month_diff_handles_whole_year_boundary():
    # Given an offset exactly one year before the current republic-era month
    current = datetime.datetime.now()
    total = (current.year - 1911) * 12 + current.month
    back_month = total - 12

    # When computing the diff
    calc_year, calc_month = utils.month_diff(back_month)

    # Then remainder month 0 is normalized to 12 and the difference is one year
    assert calc_month == 12
    assert calc_year == 0
