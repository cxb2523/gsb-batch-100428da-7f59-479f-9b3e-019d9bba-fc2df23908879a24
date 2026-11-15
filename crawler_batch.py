#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开盘啦数据爬虫 - 批量抓取版（并行）
独立运行：token 校验/刷新 + 交易日获取 + 并行抓取
反爬：随机延时 + 并发控制 + 失败重试

抓取容错约定：
- 单个板块的任一接口重试耗尽只影响该接口，不会中断当天其余板块的抓取；
- 每个板块、每个接口的结局（成功/失败/跳过、尝试次数、重试次数、原因）与当天
  三个业务 CSV 一起落盘，见 fetch_status_{date}.csv / fetch_summary_{date}.csv；
- 子板块返回空 List 属正常回退（用排行数据补父板块自身），与子板块请求失败区分；
- 子板块请求失败时该板块的成分股整体跳过，并在结局文件里如实记录。
三个业务 CSV 的列名、字段顺序与金额换算保持原有行为不变。
"""
import os
os.environ['PYTHONWARNINGS'] = 'ignore'

import requests
import json
import time
import re
import sys
import random
import subprocess
from collections import OrderedDict, namedtuple
from datetime import datetime
from pathlib import Path

import pandas as pd
try:
    import akshare as ak
except ImportError:  # 可选依赖：只有获取交易日列表时需要
    ak = None
from concurrent.futures import ThreadPoolExecutor, as_completed


# Windows 控制台默认 GBK 无法输出 emoji，日志输出失败不能影响抓取与结局记录
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except Exception:
        pass


# ── 路径 ──────────────────────────────────────
# 项目根目录 (开盘啦_数据解析/)
ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
# mitm 脚本位置
SCRIPTS = ROOT / "scripts"
CAPTURES = ROOT / "captures"
DATA_DIR = ROOT / "data"

# ── 认证参数（从 .env 加载） ───────────────────
DEVICE_ID = "80ca7d1b-2a24-3cd0-a915-99b61f6f88aa"
VERSION = "5.23.0.4"
API_VERSION = "w44"
PHONE_OS_NEW = "1"
USER_ID = ""
TOKEN = ""


def load_env():
    global USER_ID, TOKEN, DEVICE_ID
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().strip().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                key = k.strip()
                val = v.strip()
                if key == "KPL_USER_ID":
                    USER_ID = val
                elif key == "KPL_TOKEN":
                    TOKEN = val
                elif key == "KPL_DEVICE_ID":
                    DEVICE_ID = val


def save_env(key, value):
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


# ── API 常量 ───────────────────────────────────
API_URL = "https://apphis.longhuvip.com/w1/api/index.php"
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; ALN-AL00 Build/W528JS)",
    "Host": "apphis.longhuvip.com",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
}


# ── Token 相关 ─────────────────────────────────
API_HQ = "https://apphwshhq.longhuvip.com/w1/api/index.php"  # 实时行情端点（用于验证 token）


def validate_token():
    """用实时行情端点验证 token 是否过期（与原版 daily_grab_new.py 一致）"""
    params = {
        "PhoneOSNew": PHONE_OS_NEW, "DeviceID": DEVICE_ID,
        "VerSion": VERSION, "apiv": API_VERSION,
        "UserID": USER_ID, "Token": TOKEN,
        "a": "GetInfo", "c": "Index", "View": "1",
    }
    body = "&".join(f"{k}={v}" for k, v in params.items())
    # 验证用 HEADERS_HQ（不带 Host，与原版一致）
    headers_hq = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; ALN-AL00 Build/W528JS)",
        "Connection": "Keep-Alive",
    }
    try:
        r = requests.post(API_HQ, data=body, headers=headers_hq, timeout=10)
        resp = r.json()
        ec = str(resp.get("errcode", ""))
        if ec == "0":
            return True
        errmsg = resp.get("errmsg", "").lower()
        if any(kw in errmsg for kw in ["token", "expired", "invalid", "未登录", "过期"]):
            return False
        return ec == "0"
    except Exception:
        return False


def extract_token_from_captures():
    for f in sorted(CAPTURES.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            body = data.get("request_body", "")
            if "Token=" not in body:
                continue
            token = uid = None
            for part in body.split("&"):
                if part.startswith("Token="):
                    token = part.split("=", 1)[1]
                elif part.startswith("UserID="):
                    uid = part.split("=", 1)[1]
            if token and uid:
                return uid, token
        except Exception:
            continue
    return None, None


def refresh_token():
    print("\n=== Token 过期 ===")
    mitm_script = SCRIPTS / "mitm_capture.py"
    if mitm_script.exists():
        subprocess.Popen([sys.executable, str(mitm_script)])
        print("mitmproxy 已启动")
    else:
        print(f"找不到 {mitm_script}，请手动启动 mitmproxy 抓包")

    # 自动轮询捕获目录，不需要手动输入
    uid, token = None, None
    while True:
        uid, token = extract_token_from_captures()
        if token:
            break
        print("⏳ 等待新捕获...（在模拟器中打开开盘啦并登录）")
        time.sleep(5)

    if not token:
        print("未能获取新 Token，退出")
        sys.exit(1)

    save_env("KPL_TOKEN", token)
    if uid:
        save_env("KPL_USER_ID", uid)
    TOKEN = token
    USER_ID = uid or USER_ID
    print(f"✅ Token 已更新: {token[:8]}...\n")
    return USER_ID, TOKEN


# ── 交易日 ─────────────────────────────────────
def get_trading_days(start_date=None, end_date=None):
    if ak is None:
        raise RuntimeError("缺少 akshare 依赖，无法获取交易日列表；"
                           "请 pip install akshare 或直接调用 crawl_date(date)")
    df = ak.tool_trade_date_hist_sina()
    all_days = sorted(df["trade_date"].tolist())
    all_days = [v.strftime('%Y-%m-%d') for v in all_days
                if v.strftime('%Y-%m-%d') <= datetime.now().strftime('%Y-%m-%d')]
    today = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        return [all_days[-1]]
    if not end_date:
        end_date = today
    return [d for d in all_days if start_date <= d <= end_date]


# ── 响应解析 ───────────────────────────────────
def _parse_response(resp_text):
    decoded = resp_text.encode('utf-8').decode('raw_unicode_escape')
    cleaned = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', decoded)
    return json.loads(cleaned)


# ── 请求参数构建 ───────────────────────────────
def _build_params(**kwargs):
    """构建 form-urlencoded body 字符串（与原代码一致的尾部 &）"""
    return "&".join(f"{k}={v}" for k, v in kwargs.items()) + "&"


# ── 防爬参数 ───────────────────────────────────
_RETRY_MAX = 3
_DELAY_MIN = 0.05
_DELAY_MAX = 0.15
_RETRY_SLEEP = 1.5


class RequestFailed(Exception):
    """请求在重试耗尽后仍失败。保留接口名、尝试次数与最后一次错误信息。"""

    def __init__(self, api, label, attempts, last_error):
        self.api = api
        self.label = label
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"{api}[{label}] 尝试{attempts}次仍失败: {last_error}")


# 成功响应：data 为解析后的 JSON，attempts 为实际尝试次数（1 表示一次成功）
ApiResponse = namedtuple('ApiResponse', ['data', 'attempts'])


def do_request(params, label="", api=None):
    """请求接口；成功返回 ApiResponse(data, attempts)，重试耗尽抛 RequestFailed。"""
    body = _build_params(**params)
    api = api or str(params.get("a", ""))
    last_err = None
    for attempt in range(1, _RETRY_MAX + 1):
        try:
            time.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))
            resp = requests.post(API_URL, data=body, headers=HEADERS, timeout=15)
            return ApiResponse(_parse_response(resp.text), attempt)
        except Exception as e:
            last_err = e
            print(f"  [⚠ {label}] 第{attempt}/{_RETRY_MAX}次失败: {e}", flush=True)
            if attempt < _RETRY_MAX:
                time.sleep(_RETRY_SLEEP)
    print(f"  [✗ {label}] 尝试{_RETRY_MAX}次仍失败: {last_err}", flush=True)
    raise RequestFailed(api, label, _RETRY_MAX, last_err)


def _call(params, label, api):
    """调用 do_request 并把返回值统一成 ApiResponse。

    do_request 正常会抛 RequestFailed 或返回 ApiResponse；若被外部替换实现
    （旧调用方/测试桩）以返回 None 表示重试耗尽，这里同样按最终失败处理，
    避免把失败误当成“成功但无数据”。
    """
    res = do_request(params, label, api=api)
    if res is None:
        raise RequestFailed(api, label, _RETRY_MAX, "接口返回空响应（重试耗尽）")
    if isinstance(res, ApiResponse):
        return res
    return ApiResponse(res, None)


# ── 列定义（同时用于校验响应宽度与构建 DataFrame，避免二者漂移） ──
# 注意：列名、顺序与金额换算均保持现有行为不变。
RANKING_COLS = ['父代码', '父板块', '强度', '涨幅', '涨速', '成交额', '主力净额', '主力买', '主力卖',
                '量比', '流通市值', 'x1', '300 万大单净额', '总市值', '第一季度机构增仓',
                '2026 年平均 PE', '2027 年平均 PE', 'x2', 'x3']
QJ_COLS = ['父代码', '排名', '强度', '成交额', '主力净额', '涨幅', '涨停数', '涨停封单', '大额封单']
PANKOU_COLS = ['父代码', '成交额', '换手率', '市盈率', '主力买', '主力卖', '主力净额',
               '上涨', '下跌', '平盘', '流通市值', '总市值']
BOOM_COLS = ['父代码', '爆发原因', '是否爆发']
SON_COLS = ['父代码', '子代码', '子板块', '子板块强度']
STOCK_COLS = ['父代码', '子代码', '代码', '名称', 'x2', 'x3', '所属板块标签', '价格', '涨幅', '成交额',
              '实际换手率', '涨速', '实际流通', '主力买', '主力卖', '主力净额', 'x15', 'x16', 'x17',
              'x18', '卖流占比', '净流占比', '区间涨幅', '量比', 'x23', '几天几板', 'x8', '换手率',
              'x27', 'x28', '收盘封单', '最大封单', 'x31', 'x32', 'x33', '振幅', 'x35', 'x36', 'x37',
              '总市值', '流通市值', 'x40', '领涨次数', 'x42', '机构增仓 Q1', 'x44', 'x45', 'x46',
              'x47', 'x48', 'x49', 'x50', '300 万以上大单净额', 'x52', 'x53', '市净率', 'x55',
              'x56', 'x57', 'x58', '人气值', '人气排名变化', '市盈率动', '市盈率 TTM', 'PE 静']

# 接口返回体自身的字段宽度（不含代码插入列）
_QJ_WIDTH = len(QJ_COLS) - 1          # 插入父代码 1 列
_PANKOU_WIDTH = len(PANKOU_COLS) - 1  # 插入父代码 1 列
_SONPLATE_WIDTH = len(SON_COLS) - 1   # 插入父代码 1 列
_STOCK_WIDTH = len(STOCK_COLS) - 2    # 插入父代码、子代码 2 列


# ── 结局记录 ───────────────────────────────────
STAGE_LIST = "板块列表"
STAGE_PLATE = "板块数据"
STAGE_STOCK = "成分股"

OUTCOME_OK = "成功"
OUTCOME_FAIL = "失败"
OUTCOME_SKIP = "跳过"

STATUS_COLUMNS = ['日期', '板块代码', '板块名称', '子板块代码', '阶段', '接口',
                  '结局', '尝试次数', '重试次数', '说明']
SUMMARY_COLUMNS = ['日期', '板块代码', '板块名称', '结局', '板块接口成功数', '板块接口失败数',
                   '失败接口', '成分股成功数', '成分股失败数', '成分股跳过数', '重试总次数', '说明']

_STAGE_ORDER = {STAGE_LIST: 0, STAGE_PLATE: 1, STAGE_STOCK: 2}
_API_ORDER = ['RealRankingInfo', 'GetPlate_Info_QJ', 'GetPanKou',
              'GetDayBaseFaceListZDEvnArt', 'SonPlate_Info', 'ZhiShuStockList_W8']
_API_RANK = {a: i for i, a in enumerate(_API_ORDER)}


class FetchStatus:
    """收集单日抓取结局，最终与当天数据一起落成两个 CSV。

    fetch_status_{date}.csv  —— 每个板块 × 每个接口一行的明细
                                （成功/失败/跳过 + 尝试次数 + 重试次数 + 原因）
    fetch_summary_{date}.csv —— 每个板块一行的汇总，一眼看出哪个板块有问题，
                                成功板块同样有 结局=成功 的记录
    """

    def __init__(self, date):
        self.date = date
        self.plate_names = {}
        self.plate_order = []
        self._plate_pos = {}
        self._rows = []

    def add(self, plate_code, stage, api, outcome, attempts=None, note="", son_code=""):
        """记录一条结局。attempts 为 None 表示没有发起请求（如跳过）。"""
        retries = None if attempts is None else max(0, int(attempts) - 1)
        self._rows.append({
            'plate': plate_code,
            'name': self.plate_names.get(plate_code, ""),
            'son': son_code or "",
            'stage': stage,
            'api': api,
            'outcome': outcome,
            'attempts': "" if attempts is None else attempts,
            'retries': "" if retries is None else retries,
            'note': note,
        })

    def _plate_index(self, pid):
        """板块在板块列表中的位置；板块列表阶段（无板块代码）的记录排在 -1。"""
        return self._plate_pos.get(pid, -1)

    def detail_rows(self):
        """按板块顺序 → 阶段 → 接口 → 子板块 排序，保证文件内容稳定可读。"""
        self._plate_pos = {pid: i for i, pid in enumerate(self.plate_order)}
        ordered = sorted(
            enumerate(self._rows),
            key=lambda pair: (self._plate_index(pair[1]['plate']),
                              _STAGE_ORDER.get(pair[1]['stage'], 9),
                              _API_RANK.get(pair[1]['api'], len(_API_ORDER)),
                              str(pair[1]['son']),
                              pair[0]),
        )
        return [[self.date, r['plate'], r['name'], r['son'], r['stage'], r['api'],
                 r['outcome'], r['attempts'], r['retries'], r['note']]
                for _, r in ordered]

    def summary_rows(self):
        plates = OrderedDict()
        for r in self._rows:
            if r['stage'] == STAGE_LIST:
                continue
            p = plates.setdefault(r['plate'], {
                'name': r['name'], 'api_ok': 0, 'api_fail': 0, 'failed_apis': [],
                'stock_ok': 0, 'stock_fail': 0, 'stock_skip': 0, 'retries': 0,
            })
            if isinstance(r['attempts'], int):
                p['retries'] += max(0, r['attempts'] - 1)
            if r['stage'] == STAGE_PLATE:
                if r['outcome'] == OUTCOME_OK:
                    p['api_ok'] += 1
                elif r['outcome'] == OUTCOME_FAIL:
                    p['api_fail'] += 1
                    p['failed_apis'].append(r['api'])
            elif r['stage'] == STAGE_STOCK:
                if r['outcome'] == OUTCOME_OK:
                    p['stock_ok'] += 1
                elif r['outcome'] == OUTCOME_FAIL:
                    p['stock_fail'] += 1
                elif r['outcome'] == OUTCOME_SKIP:
                    p['stock_skip'] += 1

        rows = []
        order = {pid: i for i, pid in enumerate(self.plate_order)}
        for pid in sorted(plates, key=lambda p: (order.get(p, len(order)), str(p))):
            p = plates[pid]
            ok = p['api_fail'] == 0 and p['stock_fail'] == 0 and p['stock_skip'] == 0
            notes = []
            if p['failed_apis']:
                notes.append("失败接口: " + ", ".join(p['failed_apis']))
            if p['stock_skip']:
                notes.append("成分股因板块子板块请求失败而跳过")
            if p['stock_fail']:
                notes.append(f"成分股失败 {p['stock_fail']} 项")
            rows.append([
                self.date, pid, p['name'], OUTCOME_OK if ok else OUTCOME_FAIL,
                p['api_ok'], p['api_fail'], ", ".join(p['failed_apis']),
                p['stock_ok'], p['stock_fail'], p['stock_skip'], p['retries'],
                "；".join(notes),
            ])
        return rows

    def write(self, data_dir):
        """落盘结局明细与汇总，返回两个文件路径。"""
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        status_path = data_dir / f"fetch_status_{self.date}.csv"
        summary_path = data_dir / f"fetch_summary_{self.date}.csv"
        # 结局文件供事后人工核对，utf-8-sig 便于 Excel 直接打开中文
        pd.DataFrame(self.detail_rows(), columns=STATUS_COLUMNS).to_csv(
            status_path, index=False, encoding='utf-8-sig')
        pd.DataFrame(self.summary_rows(), columns=SUMMARY_COLUMNS).to_csv(
            summary_path, index=False, encoding='utf-8-sig')
        return status_path, summary_path

    def counts(self):
        rows = self.summary_rows()
        return {
            'plates': len(rows),
            'ok': sum(1 for r in rows if r[3] == OUTCOME_OK),
            'failed': sum(1 for r in rows if r[3] == OUTCOME_FAIL),
            'api_fail': sum(1 for r in self._rows
                            if r['stage'] == STAGE_PLATE and r['outcome'] == OUTCOME_FAIL),
            'stock_fail': sum(1 for r in self._rows
                              if r['stage'] == STAGE_STOCK and r['outcome'] == OUTCOME_FAIL),
            'stock_skip': sum(1 for r in self._rows
                              if r['stage'] == STAGE_STOCK and r['outcome'] == OUTCOME_SKIP),
        }


# ── 响应校验（结构异常视为失败，不能静默当成空数据） ──
def _validate_plate_response(key, payload, date):
    """校验单个板块接口的返回体，返回 (是否可用, 说明)。

    只要求字段数不少于构建 CSV 所需的前缀宽度：多余尾部字段被忽略，
    这样接口偶尔多返回字段也不会让整个板块的数据作废。
    """
    if not isinstance(payload, dict):
        return False, "响应不是 JSON 对象"

    if key == 'qj':
        if 'List' not in payload:
            return False, "响应缺少 List 字段"
        row = payload['List']
        if not isinstance(row, list) or len(row) < _QJ_WIDTH:
            return False, f"List 字段数不符（至少需要 {_QJ_WIDTH} 个）"
        return True, ""

    if key == 'pankou':
        if 'pankou' not in payload:
            return False, "响应缺少 pankou 字段"
        row = payload['pankou']
        if not isinstance(row, list) or len(row) < _PANKOU_WIDTH:
            return False, f"pankou 字段数不符（至少需要 {_PANKOU_WIDTH} 个）"
        return True, ""

    if key == 'boomreason':
        if 'List' not in payload:
            return False, "响应缺少 List 字段"
        lst = payload['List']
        if not isinstance(lst, list):
            return False, "List 不是数组"
        for item in lst:
            if not isinstance(item, dict):
                return False, "List 元素不是对象"
        if not any(item.get('Date') == date for item in lst):
            return True, "当日无爆发原因记录"  # 无当日记录属正常情况
        return True, ""

    if key == 'sonplate':
        if 'List' not in payload:
            return False, "响应缺少 List 字段"
        lst = payload['List']
        if not isinstance(lst, list):
            return False, "List 不是数组"
        for item in lst:
            if not isinstance(item, list) or len(item) < _SONPLATE_WIDTH:
                return False, f"List 元素字段数不符（至少需要 {_SONPLATE_WIDTH} 个）"
        if not lst:
            # 空子板块是正常回退，不是失败
            return True, "子板块为空，回退父板块自身"
        return True, ""

    return True, ""


def _validate_stock_response(payload):
    """校验成分股返回体，返回 (是否可用, 说明)。"""
    if not isinstance(payload, dict):
        return False, "响应不是 JSON 对象"
    if 'list' not in payload:
        return False, "响应缺少 list 字段"
    lst = payload['list']
    if not isinstance(lst, list):
        return False, "list 不是数组"
    for item in lst:
        if not isinstance(item, list) or len(item) < _STOCK_WIDTH:
            return False, f"成分股字段数不符（至少需要 {_STOCK_WIDTH} 个）"
    return True, ""


# ── 单板块并发抓取 ────────────────────────────
def _plate_requests(parent_code, date):
    """一个板块需要抓取的 4 个接口：(key, 接口名, 请求参数)。"""
    return [
        ('qj', 'GetPlate_Info_QJ', {
            "a": "GetPlate_Info_QJ", "c": "ZhiShuRanking",
            "PhoneOSNew": PHONE_OS_NEW, "DeviceID": DEVICE_ID,
            "VerSion": VERSION, "Date": date, "apiv": API_VERSION,
            "PlateID": parent_code, "RStart": "0925", "REnd": "1500",
        }),
        ('pankou', 'GetPanKou', {
            "a": "GetPanKou", "c": "ZhiShuL2Data",
            "PhoneOSNew": PHONE_OS_NEW, "DeviceID": DEVICE_ID,
            "VerSion": VERSION, "Token": TOKEN, "apiv": API_VERSION,
            "StockID": parent_code, "UserID": USER_ID, "Day": date,
        }),
        ('boomreason', 'GetDayBaseFaceListZDEvnArt', {
            "a": "GetDayBaseFaceListZDEvnArt", "c": "ZhiShuKLine",
            "PhoneOSNew": PHONE_OS_NEW, "DeviceID": DEVICE_ID,
            "VerSion": VERSION, "Token": TOKEN, "apiv": API_VERSION,
            "StockID": parent_code, "UserID": USER_ID,
            'Index': '0', 'st': '10', 'Type': '0', 'IsBoom': '0',
        }),
        ('sonplate', 'SonPlate_Info', {
            "a": "SonPlate_Info", "c": "ZhiShuRanking",
            "PhoneOSNew": PHONE_OS_NEW, "DeviceID": DEVICE_ID,
            "VerSion": VERSION, "IsShow": "1", "Date": date,
            "apiv": API_VERSION, "PlateID": parent_code,
        }),
    ]


def fetch_plate_data(parent_code, date):
    """并发获取一个板块的 4 组关联数据。

    每个接口独立重试、独立记录结局：任一接口最终失败都不影响其余接口，
    也不会影响其他板块。返回 (data, outcomes)，
    data 中失败接口的值为 None（用于区分“请求失败”与“返回空列表”）。
    """
    data = {}
    outcomes = []
    for key, api, params in _plate_requests(parent_code, date):
        try:
            res = _call(params, f"{api}[{parent_code}]", api)
        except RequestFailed as e:
            data[key] = None
            outcomes.append({'api': api, 'outcome': OUTCOME_FAIL, 'attempts': e.attempts,
                             'note': f"请求失败: {e.last_error}"})
            continue
        except Exception as e:  # 兜底：单接口异常也只影响该接口
            data[key] = None
            outcomes.append({'api': api, 'outcome': OUTCOME_FAIL, 'attempts': None,
                             'note': f"异常: {e}"})
            continue

        ok, note = _validate_plate_response(key, res.data, date)
        if ok:
            data[key] = res.data
            outcomes.append({'api': api, 'outcome': OUTCOME_OK,
                             'attempts': res.attempts, 'note': note})
        else:
            data[key] = None
            outcomes.append({'api': api, 'outcome': OUTCOME_FAIL,
                             'attempts': res.attempts, 'note': note})
    return data, outcomes


def fetch_stock_list(plate_id, son_plate_id, date):
    """抓取一个子板块的成分股。成功返回 ApiResponse，重试耗尽抛 RequestFailed。"""
    return _call({
        "Order": "1", "TSZB": "0", "a": "ZhiShuStockList_W8", "st": "30",
        "c": "ZhiShuRanking", "PhoneOSNew": PHONE_OS_NEW, "old": "1",
        "DeviceID": DEVICE_ID, "VerSion": VERSION, "IsZZ": "0",
        "Token": TOKEN, "Index": "0", "Date": date,
        "apiv": API_VERSION, "Type": "6", "IsKZZType": "0",
        "UserID": USER_ID, "PlateID": son_plate_id,
        "TSZB_Type": "0", "filterType": "0",
    }, f"StockList[{son_plate_id}]", api='ZhiShuStockList_W8')


def _ranking_fallback(js_real_ranking_info, pid):
    """子板块为空时，从排行数据取父板块自身的 [名称, 强度] 用于回退。"""
    for item in js_real_ranking_info.get("list", []):
        if isinstance(item, list) and item and item[0] == pid:
            a, b = item[1:3]
            return a, b
    return None, None


def _first_ranking_code(js_real_ranking_info):
    """排行数据第一行的板块代码（用于分页去重判断）；无数据时返回 None。"""
    lst = js_real_ranking_info.get("list") if isinstance(js_real_ranking_info, dict) else None
    if lst and isinstance(lst[0], list) and lst[0]:
        return str(lst[0][0])
    return None


# ── 单日期抓取（三个业务 CSV 输出与原有行为保持一致） ─

def _build_frame(rows, columns, label, date):
    """按固定列宽构建 DataFrame。

    接口偶尔多返回尾部字段时截断为所需前缀；字段数不足的行跳过并告警。
    这样单条脏数据不会让当天的 CSV 整体作废。
    """
    expected = len(columns)
    good = []
    dropped = trimmed = 0
    for r in rows:
        if not isinstance(r, list):
            dropped += 1
        elif len(r) < expected:
            dropped += 1
        elif len(r) > expected:
            good.append(r[:expected])
            trimmed += 1
        else:
            good.append(r)
    if dropped:
        print(f"  [{date}] ⚠ {label}：跳过 {dropped} 条字段数不符（不足）的记录", flush=True)
    if trimmed:
        print(f"  [{date}] ⚠ {label}：{trimmed} 条记录尾部多余字段已忽略", flush=True)
    return pd.DataFrame(good, columns=columns)


def crawl_date(date, max_workers=10):
    """抓取单个交易日。

    任何单板块、单接口的失败都只影响该板块对应的数据，其余板块照常抓取并写出
    三个业务 CSV；结局另写入 fetch_status/fetch_summary 两个文件。
    """
    data_dir = Path(DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    js_real_ranking_info = {}
    boomreason_by_plate = {}   # pid -> [爆发原因, 是否爆发]，按板块顺序聚合
    status = FetchStatus(date)

    # ── 阶段1：串行分页 RealRankingInfo ──
    all_plate_ids = []
    page = 0
    page_attempts = 0
    listing_failed = False
    while True:
        try:
            res = _call({
                "Order": "1", "a": "RealRankingInfo", "st": "30", "c": "ZhiShuRanking",
                "PhoneOSNew": PHONE_OS_NEW, "DeviceID": DEVICE_ID,
                "VerSion": VERSION, "Index": f"{page * 30}",
                "Date": date, "apiv": API_VERSION, "Type": "1", "ZSType": "7",
            }, f"RealRankingInfo[{date}@{page}]", 'RealRankingInfo')
        except RequestFailed as e:
            status.add('', STAGE_LIST, e.api, OUTCOME_FAIL, e.attempts,
                       f"第 {page} 页请求失败: {e.last_error}")
            if page == 0:
                listing_failed = True
            else:
                # 后续页失败：保留已拿到的板块列表，继续抓其余数据
                print(f"  [{date}] ⚠ 第 {page} 页板块列表失败，"
                      f"使用已获取的 {len(all_plate_ids)} 个板块继续", flush=True)
            break

        page_attempts += res.attempts or 0
        res_i = res.data
        if not isinstance(res_i, dict) or not isinstance(res_i.get("list"), list):
            status.add('', STAGE_LIST, 'RealRankingInfo', OUTCOME_FAIL, res.attempts,
                       f"第 {page} 页响应缺少 list 数组")
            if page == 0:
                listing_failed = True
            break

        items = res_i.get("list", [])
        if len(items) == 0:
            if page == 0:
                status.add('', STAGE_LIST, 'RealRankingInfo', OUTCOME_OK, res.attempts,
                           "当日无板块数据")
            break

        # 去重停止（与原代码一致：转为字符串做 substring 匹配）
        first = items[0]
        prev_first_code = _first_ranking_code(js_real_ranking_info)
        if (prev_first_code is not None and isinstance(first, list) and first
                and str(first[0]) in prev_first_code):
            break

        # 合并排行数据
        if js_real_ranking_info:
            js_real_ranking_info['list'].extend(items)
        else:
            js_real_ranking_info = {k: v for k, v in res_i.items()}

        # 收集 plate_id
        for item in items:
            if isinstance(item, list) and item and item[0] not in all_plate_ids:
                all_plate_ids.append(item[0])

        page += 1
        time.sleep(random.uniform(0.3, 0.6))

    if not listing_failed and all_plate_ids:
        status.add('', STAGE_LIST, 'RealRankingInfo', OUTCOME_OK, page_attempts,
                   f"共 {len(all_plate_ids)} 个板块（{page} 页）")

    if listing_failed or not all_plate_ids:
        status.write(data_dir)
        print(f"  [{date}] 无可用板块数据，跳过业务 CSV（结局见 fetch_status/fetch_summary）")
        return

    plate_names = {}
    for item in js_real_ranking_info.get("list", []):
        if isinstance(item, list) and len(item) >= 2:
            plate_names[item[0]] = item[1]
    status.plate_names = plate_names
    status.plate_order = list(all_plate_ids)

    print(f"  [{date}] 共 {len(all_plate_ids)} 个板块")

    # ── 阶段2：并行抓取板块数据 ──
    qj_rows = OrderedDict()       # pid -> 已插入父代码的行
    pankou_rows = OrderedDict()   # pid -> 已插入父代码的行
    son_rows_by_plate = {}        # pid -> 子板块行列表（含空子板块回退行）
    son_failed = set()            # SonPlate_Info 最终失败的父板块

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_pid = {
            executor.submit(fetch_plate_data, pid, date): pid
            for pid in all_plate_ids
        }

        for future in as_completed(future_to_pid):
            pid = future_to_pid[future]
            try:
                data, outcomes = future.result()
            except Exception as e:
                # 兜底：即使抓取函数本身异常，也只影响这一个板块
                print(f"  [✗ {pid}] 板块数据异常: {e}", flush=True)
                for _key, api, _params in _plate_requests(pid, date):
                    status.add(pid, STAGE_PLATE, api, OUTCOME_FAIL, None,
                               f"板块抓取异常: {e}")
                son_failed.add(pid)
                continue

            for o in outcomes:
                status.add(pid, STAGE_PLATE, o['api'], o['outcome'],
                           o['attempts'], o['note'])

            # -- QJ --
            res_qj = data.get('qj')
            if res_qj:
                row = list(res_qj['List'])
                row.insert(0, pid)
                qj_rows[pid] = row

            # -- PanKou --
            res_pk = data.get('pankou')
            if res_pk:
                row = list(res_pk['pankou'])
                row.insert(0, res_pk.get('code', pid))
                pankou_rows[pid] = row

            # -- BoomReason --
            res_br = data.get('boomreason')
            if res_br:
                for item in res_br.get('List', []):
                    if item.get('Date') == date:
                        reason = str(item.get('BoomReason', '')).strip()
                        boomreason_by_plate[pid] = [reason, item.get('IsBoom')]
                        break

            # -- SonPlate --
            res_sp = data.get('sonplate')
            if res_sp is None:
                # 请求失败：记为失败，该板块无子板块数据，成分股需跳过。
                # 绝不能伪装成“子板块为空”的回退。
                son_failed.add(pid)
            elif len(res_sp["List"]) == 0:
                # 子板块为空：正常回退，用排行数据补父板块自身
                name, strength = _ranking_fallback(js_real_ranking_info, pid)
                son_rows_by_plate[pid] = [[pid, pid, name, strength]]
            else:
                son_rows_by_plate[pid] = [[pid] + list(item) for item in res_sp["List"]]

    # 子板块按板块列表顺序聚合，输出行序稳定
    son_rows = [row for pid in all_plate_ids
                for row in son_rows_by_plate.get(pid, [])]
    js_son_plate_info = {'List': son_rows} if son_rows else {}

    # ── 阶段3：并行抓取成分股 ──
    # 成分股依赖子板块结果：子板块请求失败的板块整体跳过，并在结局里如实记录
    for pid in all_plate_ids:
        if pid in son_failed:
            status.add(pid, STAGE_STOCK, 'ZhiShuStockList_W8', OUTCOME_SKIP, None,
                       "子板块请求失败，成分股跳过")

    stock_targets = [(row[0], row[1]) for row in son_rows
                     if len(row) >= 2 and row[0] not in son_failed]

    all_stock_items = []
    if stock_targets:
        stock_items_by_target = {}  # 目标下标 -> 行列表（同一子板块重复出现时不会互相覆盖）
        with ThreadPoolExecutor(max_workers=max_workers) as executor2:
            stock_futures = {
                executor2.submit(fetch_stock_list, pid_key, son_key, date): (idx, pid_key, son_key)
                for idx, (pid_key, son_key) in enumerate(stock_targets)
            }
            for future in as_completed(stock_futures):
                idx, pid_key, son_key = stock_futures[future]
                try:
                    res_st = future.result()
                except RequestFailed as e:
                    status.add(pid_key, STAGE_STOCK, e.api, OUTCOME_FAIL, e.attempts,
                               f"请求失败: {e.last_error}", son_code=son_key)
                    continue
                except Exception as e:
                    print(f"  [✗ StockList({son_key})] 异常: {e}", flush=True)
                    status.add(pid_key, STAGE_STOCK, 'ZhiShuStockList_W8', OUTCOME_FAIL,
                               None, f"异常: {e}", son_code=son_key)
                    continue

                ok, note = _validate_stock_response(res_st.data)
                if not ok:
                    status.add(pid_key, STAGE_STOCK, 'ZhiShuStockList_W8', OUTCOME_FAIL,
                               res_st.attempts, note, son_code=son_key)
                    continue

                items = res_st.data['list']
                status.add(pid_key, STAGE_STOCK, 'ZhiShuStockList_W8', OUTCOME_OK,
                           res_st.attempts, f"成分股 {len(items)} 只", son_code=son_key)
                rows = []
                for item in items:
                    row = list(item)
                    row.insert(0, pid_key)
                    row.insert(1, son_key)
                    rows.append(row)
                stock_items_by_target[idx] = rows

        # 按子板块抓取顺序聚合，输出行序稳定
        for idx in range(len(stock_targets)):
            all_stock_items.extend(stock_items_by_target.get(idx, []))

    js_zhishu_stock_list = {'list': all_stock_items} if all_stock_items else {}

    # ── 阶段 4：构建 DataFrame（列名/字段顺序/金额换算与原有行为一致） ──

    # 按板块顺序聚合，保证输出稳定
    js_plate_info_qj = {'List': [qj_rows[pid] for pid in all_plate_ids if pid in qj_rows]}
    js_pan_kou = {'pankou': [pankou_rows[pid] for pid in all_plate_ids if pid in pankou_rows]}

    df_real_ranking_info = _build_frame(js_real_ranking_info.get("list", []),
                                        RANKING_COLS, '申报排行', date)
    df_real_ranking_info = df_real_ranking_info[[v for v in df_real_ranking_info.columns if v.find('x') != 0]]
    # 金额类字段转换为亿为单位
    for col in ['成交额', '主力净额', '主力买', '主力卖', '流通市值', '300 万大单净额', '总市值']:
        if col in df_real_ranking_info.columns:
            df_real_ranking_info[col] = pd.to_numeric(df_real_ranking_info[col], errors='coerce') / 1e8

    df_plate_info_qj = _build_frame(js_plate_info_qj.get("List", []),
                                    QJ_COLS, '板块强度', date)
    df_plate_info_qj = df_plate_info_qj[['父代码', '排名', '涨停数', '涨停封单', '大额封单']]
    # 金额类字段转换为亿为单位
    for col in ['成交额', '主力净额', '涨停封单', '大额封单']:
        if col in df_plate_info_qj.columns:
            df_plate_info_qj[col] = pd.to_numeric(df_plate_info_qj[col], errors='coerce') / 1e8

    df_pan_kou = _build_frame(js_pan_kou.get("pankou", []),
                              PANKOU_COLS, '盘口', date)
    df_pan_kou = df_pan_kou[['父代码', '换手率', '市盈率', '上涨', '下跌', '平盘']]
    # 金额类字段转换为亿为单位（虽然这些列最终被过滤，但保持一致性）
    for col in ['成交额', '主力买', '主力卖', '主力净额', '流通市值', '总市值']:
        if col in df_pan_kou.columns:
            df_pan_kou[col] = pd.to_numeric(df_pan_kou[col], errors='coerce') / 1e8

    boom_rows = [[pid] + boomreason_by_plate[pid] for pid in all_plate_ids
                 if pid in boomreason_by_plate]
    df_boomreason = _build_frame(boom_rows, BOOM_COLS, '爆发原因', date)

    df_son_plate_info = _build_frame(js_son_plate_info.get("List", []),
                                     SON_COLS, '子板块', date)

    df_zhishu_stock_list = _build_frame(js_zhishu_stock_list.get('list', []),
                                        STOCK_COLS, '成分股', date)
    df_zhishu_stock_list = df_zhishu_stock_list[[v for v in df_zhishu_stock_list.columns if v.find('x') != 0]]
    # 金额类字段转换为亿为单位
    for col in ['成交额', '主力买', '主力卖', '主力净额', '收盘封单', '最大封单', '总市值', '流通市值', '300 万以上大单净额']:
        if col in df_zhishu_stock_list.columns:
            df_zhishu_stock_list[col] = pd.to_numeric(df_zhishu_stock_list[col], errors='coerce') / 1e8

    # merge（与原代码完全一致）
    df_plate_info = df_real_ranking_info.merge(df_plate_info_qj, how='left', on='父代码')
    df_plate_info = df_plate_info.merge(df_pan_kou, how='left', on='父代码')
    df_plate_info = df_plate_info.merge(df_boomreason, how='left', on='父代码')

    # 写 CSV（与原代码完全一致：index=False，无 encoding 参数）
    df_plate_info.to_csv(data_dir / f'main_plate_info_{date}.csv', index=False)
    df_son_plate_info.to_csv(data_dir / f'sub_plate_info_{date}.csv', index=False)
    df_zhishu_stock_list.to_csv(data_dir / f'stock_info_{date}.csv', index=False)

    # 结局落盘：明细 + 汇总，与当天数据一起产出
    status_path, summary_path = status.write(data_dir)
    counts = status.counts()
    elapsed = time.time() - start_time
    print(f"  [{date}] ✅ 抓取完成，耗时 {int(elapsed//60)}分{int(elapsed%60)}秒。"
          f"板块 {counts['plates']} 个：成功 {counts['ok']}，失败 {counts['failed']}；"
          f"接口失败 {counts['api_fail']}，成分股失败 {counts['stock_fail']}，"
          f"成分股跳过 {counts['stock_skip']}")
    print(f"  [{date}] 结局明细: {status_path}")
    print(f"  [{date}] 结局汇总: {summary_path}")


# ── 主入口 ─────────────────────────────────────
def main(start_date=None, end_date=None, delay=None, workers=None):
    # 如果未传参，从命令行解析
    if start_date is None and end_date is None:
        args = sys.argv[1:]
        i = 0
        while i < len(args):
            if args[i] == "--start" and i + 1 < len(args):
                start_date = args[i + 1]; i += 2
            elif args[i] == "--end" and i + 1 < len(args):
                end_date = args[i + 1]; i += 2
            elif args[i] == "--delay" and i + 1 < len(args):
                delay = float(args[i + 1]); i += 2
            elif args[i] == "--workers" and i + 1 < len(args):
                workers = int(args[i + 1]); i += 2
            else:
                i += 1

    if delay is None:
        delay = float(os.environ.get("CRAWL_DELAY", "0.05"))
    if workers is None:
        workers = int(os.environ.get("CRAWL_WORKERS", "10"))

    global _DELAY_MIN, _DELAY_MAX
    _DELAY_MIN = delay
    _DELAY_MAX = delay * 3

    if start_date or end_date:
        dates = get_trading_days(start_date, end_date)
    else:
        dates = get_trading_days()

    if not dates:
        print("无交易日，退出")
        return

    print(f"抓取区间: {dates[0]} ~ {dates[-1]} 共 {len(dates)} 天\n")

    load_env()

    if not validate_token():
        refresh_token()

    for date in dates:
        print(f"\n{'=' * 60}")
        print(f"📅 抓取日期: {date}")
        try:
            crawl_date(date, max_workers=workers)
        except Exception as e:
            # 单个日期出现未预期异常时继续跑其余日期，避免整批数据作废
            print(f"  [{date}] ✗ 抓取异常: {e}", flush=True)

    print(f"\n{'=' * 60}")
    print("✅ 全部抓取完成!")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main(start_date="2026-05-01", end_date="2026-05-15")
