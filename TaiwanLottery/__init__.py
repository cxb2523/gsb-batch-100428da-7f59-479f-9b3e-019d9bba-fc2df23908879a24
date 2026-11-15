# -*- coding: utf-8 -*-
from dataclasses import dataclass, field

import requests

from TaiwanLottery import utils


class CrawlStatus:
    # 成功解析且有資料
    SUCCESS = '成功'
    # 該月查無資料（totalSize 為 0）
    NO_DATA = '無資料'
    # 網路異常、非 2xx 或回應內容不是合法 JSON
    REQUEST_FAILED = '請求失敗'
    # JSON 可解析但缺少 content / 結果鍵，或 totalSize 與實際陣列長度不一致
    STRUCTURE_ERROR = '結構異常'


@dataclass
class LotteryResult:
    # 彩券名稱，例如「威力彩」
    lottery_name: str
    # 請求年月，例如 ['2023', '06']
    request_year_month: list
    # 抓取狀態，參考 CrawlStatus
    status: str
    # 已成功解析的記錄列表
    records: list = field(default_factory=list)
    # 失敗原因簡述；成功或無資料時為空字串
    error_message: str = ''


class RequestError(Exception):
    # 網路異常、HTTP 非 2xx 或回應內容不是合法 JSON
    pass


class StructureError(Exception):
    # JSON 可解析，但內容結構不符合預期；records 保留出事前已解析的記錄
    def __init__(self, message, records=None):
        super().__init__(message)
        self.records = list(records) if records else []


class TaiwanLotteryCrawler():
    BASE_URL = 'https://api.taiwanlottery.com/TLCAPIWeB/Lottery'
    COUNT_OF_GROUP_1 = 6

    def get_lottery_result(self, url):
        try:
            response = requests.get(url)
        except requests.exceptions.RequestException as error:
            raise RequestError('網路請求異常: {}'.format(error))

        if not response.ok:
            raise RequestError('HTTP 狀態碼 {}，非 2xx 成功回應'.format(response.status_code))

        try:
            return response.json()
        except ValueError as error:
            raise RequestError('回應內容不是合法 JSON: {}'.format(error))

    # 統一的抓取流程：把請求/結構異常轉成可核對的 LotteryResult
    def _crawl(self, lottery_name, back_time, url, result_key, row_builder):
        try:
            result = self.get_lottery_result(url)
            records = self._parse_records(result, result_key, row_builder)
        except RequestError as error:
            return LotteryResult(lottery_name, back_time, CrawlStatus.REQUEST_FAILED, [], str(error))
        except StructureError as error:
            return LotteryResult(lottery_name, back_time, CrawlStatus.STRUCTURE_ERROR, error.records, str(error))

        if len(records) == 0:
            return LotteryResult(lottery_name, back_time, CrawlStatus.NO_DATA, [])

        return LotteryResult(lottery_name, back_time, CrawlStatus.SUCCESS, records)

    # 解析 content；缺少鍵或 totalSize 不一致一律視為結構異常
    def _parse_records(self, result, result_key, row_builder):
        if not isinstance(result, dict) or 'content' not in result:
            raise StructureError("回應 JSON 缺少 'content' 鍵")

        content = result['content']
        if not isinstance(content, dict):
            raise StructureError("回應 JSON 的 'content' 不是物件")
        if 'totalSize' not in content:
            raise StructureError("回應 JSON 缺少 'content.totalSize' 鍵")
        if result_key not in content:
            raise StructureError("回應 JSON 缺少 'content.{}' 鍵".format(result_key))

        total_size = content['totalSize']
        raw_records = content[result_key]
        if not isinstance(raw_records, list):
            raise StructureError("'content.{}' 不是陣列".format(result_key))
        if total_size != len(raw_records):
            raise StructureError(
                "totalSize ({}) 與實際記錄數 ({}) 不一致".format(total_size, len(raw_records))
            )

        records = []
        for index in range(total_size):
            try:
                records.append(row_builder(raw_records[index]))
            except (KeyError, IndexError, TypeError) as error:
                raise StructureError(
                    '第 {} 筆記錄結構異常，缺少或誤用欄位: {}'.format(index, error), records
                )

        return records

    @staticmethod
    def _build_two_zone_row(row):
        return {
            "期別": row['period'],
            "開獎日期": row['lotteryDate'],
            "第一區": row['drawNumberSize'][0:TaiwanLotteryCrawler.COUNT_OF_GROUP_1],
            "第二區": row['drawNumberSize'][TaiwanLotteryCrawler.COUNT_OF_GROUP_1]
        }

    @staticmethod
    def _build_special_number_row(row):
        return {
            "期別": row['period'],
            "開獎日期": row['lotteryDate'],
            "獎號": row['drawNumberSize'][0:TaiwanLotteryCrawler.COUNT_OF_GROUP_1],
            "特別號": row['drawNumberSize'][TaiwanLotteryCrawler.COUNT_OF_GROUP_1]
        }

    @staticmethod
    def _build_draw_number_size_row(row):
        return {
            "期別": row['period'],
            "開獎日期": row['lotteryDate'],
            "獎號": row['drawNumberSize'],
        }

    @staticmethod
    def _build_draw_number_appear_row(row):
        return {
            "期別": row['period'],
            "開獎日期": row['lotteryDate'],
            "獎號": row['drawNumberAppear'],
        }

    # 威力彩
    def super_lotto(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/SuperLotto638Result?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('威力彩', back_time, URL, 'superLotto638Res', self._build_two_zone_row)

    # 大樂透
    def lotto649(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/Lotto649Result?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('大樂透', back_time, URL, 'lotto649Res', self._build_special_number_row)

    # # 今彩539
    def daily_cash(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/Daily539Result?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('今彩539', back_time, URL, 'daily539Res', self._build_draw_number_size_row)

    # # 雙贏彩
    def lotto1224(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/Lotto1224Result?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('雙贏彩', back_time, URL, 'lotto1224Res', self._build_draw_number_size_row)

    # # 3星彩
    def lotto3d(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/3DHistoryResult?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('3星彩', back_time, URL, 'lotto3DHistoryRes', self._build_draw_number_appear_row)

    # 4星彩
    def lotto4d(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/4DHistoryResult?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('4星彩', back_time, URL, 'lotto4DHistoryRes', self._build_draw_number_appear_row)

    # 38樂合彩
    def lotto38m6(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/38M6Result?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('38樂合彩', back_time, URL, 'm638Res', self._build_draw_number_size_row)

    # 49樂合彩
    def lotto49m6(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/49M6Result?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('49樂合彩', back_time, URL, 'm649Res', self._build_draw_number_size_row)

    # 39樂合彩
    def lotto39m5(self, back_time=[utils.get_current_year(), utils.get_current_month()]):
        URL = "{}/39M5Result?period&month={}-{}&pageSize=31".format(self.BASE_URL, back_time[0], back_time[1])
        return self._crawl('39樂合彩', back_time, URL, 'm539Res', self._build_draw_number_size_row)
