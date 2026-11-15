# -*- coding: utf-8 -*-
"""crawler_batch 抓取链路容错与结局记录的验收测试。

全部用本地桩请求，不依赖真实 token 与线上接口。
"""
import json

import pandas as pd
import pytest

import crawler_batch as cb
from conftest import FakeApi, install

DATE = "2026-05-08"

# 与改造前保持一致的列名与顺序（来源：改造前 crawler_batch.py 的 lst_col 与 merge）
MAIN_COLUMNS = ['父代码', '父板块', '强度', '涨幅', '涨速', '成交额', '主力净额', '主力买',
                '主力卖', '量比', '流通市值', '300 万大单净额', '总市值', '第一季度机构增仓',
                '2026 年平均 PE', '2027 年平均 PE', '排名', '涨停数', '涨停封单', '大额封单',
                '换手率', '市盈率', '上涨', '下跌', '平盘', '爆发原因', '是否爆发']
SUB_COLUMNS = ['父代码', '子代码', '子板块', '子板块强度']
STOCK_COLUMNS = ['父代码', '子代码', '代码', '名称', '所属板块标签', '价格', '涨幅', '成交额',
                 '实际换手率', '涨速', '实际流通', '主力买', '主力卖', '主力净额', '卖流占比',
                 '净流占比', '区间涨幅', '量比', '几天几板', '换手率', '收盘封单', '最大封单',
                 '振幅', '总市值', '流通市值', '领涨次数', '机构增仓 Q1', '300 万以上大单净额',
                 '市净率', '人气值', '人气排名变化', '市盈率动', '市盈率 TTM', 'PE 静']


# ── 造数工具 ───────────────────────────────────
def ranking_item(pid, name=None):
    name = name or f"板块{pid}"
    return [pid, name, "100", "1.0", "0.1", "1e9", "1e8", "5e8", "5e8", "1.2",
            "1e10", "x", "1e7", "2e10", "3", "10", "12", "x", "x"]


def qj_payload(params=None):
    # 响应 List 为 8 字段，父代码由抓取端插入
    return json.dumps({"List": [str(i) for i in range(1, cb._QJ_WIDTH + 1)]})


def pankou_payload(params):
    # 真实接口回显 code；这里按请求的 StockID 回显，便于校验父代码来源
    return json.dumps({"pankou": [str(i) for i in range(1, cb._PANKOU_WIDTH + 1)],
                       "code": params.get("StockID", "")})


def boom_payload(params=None):
    return json.dumps({"List": [{"Date": DATE, "BoomReason": "测试原因", "IsBoom": 1}]})


def son_payload(sons):
    return json.dumps({"List": sons})


def stock_payload(params=None):
    rows = []
    for i in range(2):
        row = [str(j) for j in range(cb._STOCK_WIDTH)]
        row[0] = str(600000 + i)
        rows.append(row)
    return json.dumps({"list": rows})


def build_routes(pids, sons, empty_son=()):
    """默认全部成功；empty_son 中的板块返回空子板块列表。"""
    empty = set(empty_son)
    routes = {"RealRankingInfo": json.dumps({"list": [ranking_item(p) for p in pids]})}
    routes["GetPlate_Info_QJ"] = qj_payload
    routes["GetPanKou"] = pankou_payload
    routes["GetDayBaseFaceListZDEvnArt"] = boom_payload

    def _son(params):
        pid = params.get("PlateID")
        return son_payload([] if pid in empty else sons.get(pid, []))

    routes["SonPlate_Info"] = _son
    routes["ZhiShuStockList_W8"] = stock_payload
    return routes


def fail_for(api, plate_param, value="connection reset by peer"):
    """生成 failing 判定：指定接口 + 指定参数取值持续失败。"""
    def _failing(name, params):
        if name == api and params.get(plate_param) == value:
            return "connection reset by peer"
        return None
    return _failing


def read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding='utf-8-sig')


def csv_path(tmp_path, prefix):
    return tmp_path / f"{prefix}_{DATE}.csv"


# ── 用例 ───────────────────────────────────────
def test_all_success_keeps_three_csv_schema_and_money_conversion(tmp_path, monkeypatch):
    pids = ["801001", "801002"]
    sons = {"801001": [["801101", "子A", "10"]], "801002": [["801102", "子B", "20"]]}
    api = FakeApi(build_routes(pids, sons))
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=4)

    main = read_csv(csv_path(tmp_path, "main_plate_info"))
    sub = read_csv(csv_path(tmp_path, "sub_plate_info"))
    stock = read_csv(csv_path(tmp_path, "stock_info"))

    # 列名、顺序与改造前一致
    assert list(main.columns) == MAIN_COLUMNS
    assert list(sub.columns) == SUB_COLUMNS
    assert list(stock.columns) == STOCK_COLUMNS

    assert set(main['父代码']) == set(pids)
    assert len(sub) == 2
    assert len(stock) == 4  # 2 板块 × 1 子板块 × 2 只

    # 金额换算保持 1e8 口径：成交额 1e9 → 10 亿；盘口换手率保持原值
    assert float(main.loc[0, '成交额']) == pytest.approx(10.0)
    assert float(main.loc[0, '换手率']) == 2.0


def test_success_outcome_recorded_for_every_plate_and_endpoint(tmp_path, monkeypatch):
    pids = ["801001", "801002"]
    sons = {p: [[f"{p}9", f"子{p}", "5"]] for p in pids}
    install(monkeypatch, tmp_path, FakeApi(build_routes(pids, sons)))

    cb.crawl_date(DATE, max_workers=4)

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    for pid in pids:
        rows = status[status['板块代码'] == pid]
        plate_rows = rows[rows['阶段'] == '板块数据']
        assert len(plate_rows) == 4
        assert set(plate_rows['结局']) == {'成功'}
        assert set(plate_rows['尝试次数']) == {'1'}
        assert set(plate_rows['重试次数']) == {'0'}
        assert len(rows[rows['阶段'] == '成分股']) == 1

    summary = read_csv(csv_path(tmp_path, "fetch_summary"))
    assert list(summary['板块代码']) == pids  # 顺序与板块列表一致
    assert set(summary['结局']) == {'成功'}
    # 成功板块的名称也要能对上，便于核对
    assert set(summary['板块名称']) == {f"板块{p}" for p in pids}


def test_plate_endpoint_exhausted_does_not_abort_other_plates(tmp_path, monkeypatch):
    """801002 的 GetPanKou 持续失败：其余板块照常完成，失败结局可读。"""
    pids = ["801001", "801002", "801003"]
    sons = {p: [[f"{p}9", f"子{p}", "5"]] for p in pids}
    api = FakeApi(build_routes(pids, sons),
                  failing=fail_for("GetPanKou", "StockID", "801002"))
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=4)

    # 三个业务 CSV 都已产出
    main = read_csv(csv_path(tmp_path, "main_plate_info"))
    sub = read_csv(csv_path(tmp_path, "sub_plate_info"))
    stock = read_csv(csv_path(tmp_path, "stock_info"))
    assert list(main.columns) == MAIN_COLUMNS
    assert set(main['父代码']) == set(pids)

    # 失败板块的盘口列为空，成功板块有值
    failed_row = main[main['父代码'] == '801002'].iloc[0]
    ok_row = main[main['父代码'] == '801001'].iloc[0]
    assert failed_row['换手率'] == ''
    assert ok_row['换手率'] != ''

    # 盘口失败不影响子板块与成分股
    assert len(sub) == 3
    assert len(stock) == 6

    # 明细能定位：板块 + 接口 + 尝试/重试次数 + 原因
    status = read_csv(csv_path(tmp_path, "fetch_status"))
    failed = status[(status['板块代码'] == '801002') & (status['接口'] == 'GetPanKou')].iloc[0]
    assert failed['结局'] == '失败'
    assert failed['尝试次数'] == str(cb._RETRY_MAX)
    assert failed['重试次数'] == str(cb._RETRY_MAX - 1)
    assert 'connection reset' in failed['说明']
    assert failed['板块名称'] == '板块801002'
    # 确实按重试上限发起了请求
    assert api.count("GetPanKou", StockID="801002") == cb._RETRY_MAX

    # 同一板块其他接口仍是成功，其他板块全部成功
    others = status[(status['板块代码'] == '801002') & (status['接口'] != 'GetPanKou')]
    assert set(others['结局']) == {'成功'}

    summary = read_csv(csv_path(tmp_path, "fetch_summary"))
    s2 = summary[summary['板块代码'] == '801002'].iloc[0]
    assert s2['结局'] == '失败'
    assert s2['失败接口'] == 'GetPanKou'
    assert s2['板块接口失败数'] == '1'
    assert summary[summary['板块代码'] == '801001'].iloc[0]['结局'] == '成功'
    assert summary[summary['板块代码'] == '801003'].iloc[0]['结局'] == '成功'


def test_empty_son_plate_is_normal_fallback_not_failure(tmp_path, monkeypatch):
    pids = ["801001", "801002"]
    sons = {"801002": [["801102", "子B", "20"]]}
    install(monkeypatch, tmp_path, FakeApi(build_routes(pids, sons, empty_son=["801001"])))

    cb.crawl_date(DATE, max_workers=4)

    sub = read_csv(csv_path(tmp_path, "sub_plate_info"))
    row = sub[sub['父代码'] == '801001'].iloc[0]
    assert row['子代码'] == '801001'
    assert row['子板块'] == '板块801001'
    assert row['子板块强度'] == '100'
    # 空子板块回退后仍会抓父板块自身的成分股
    stock = read_csv(csv_path(tmp_path, "stock_info"))
    assert set(stock['父代码']) == set(pids)

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    son_row = status[(status['板块代码'] == '801001') & (status['接口'] == 'SonPlate_Info')].iloc[0]
    assert son_row['结局'] == '成功'
    assert '回退' in son_row['说明']

    summary = read_csv(csv_path(tmp_path, "fetch_summary"))
    assert summary[summary['板块代码'] == '801001'].iloc[0]['结局'] == '成功'


def test_son_plate_failure_skips_stocks_and_is_recorded(tmp_path, monkeypatch):
    """子板块接口失败：记为失败，该板块成分股跳过并记录，其他板块照常。"""
    pids = ["801001", "801002"]
    sons = {"801001": [["801101", "子A", "10"]], "801002": [["801102", "子B", "20"]]}
    api = FakeApi(build_routes(pids, sons),
                  failing=fail_for("SonPlate_Info", "PlateID", "801001"))
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=4)

    sub = read_csv(csv_path(tmp_path, "sub_plate_info"))
    stock = read_csv(csv_path(tmp_path, "stock_info"))

    # 不能把失败伪装成“空子板块”的回退行
    assert '801001' not in set(sub['父代码'])
    assert set(sub['父代码']) == {'801002'}
    # 失败板块的成分股既不抓取也不伪造
    assert set(stock['父代码']) == {'801002'}
    assert len(stock) == 2
    # 没有向失败板块的子板块发起成分股请求
    assert api.count("ZhiShuStockList_W8", PlateID="801101") == 0

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    son_row = status[(status['板块代码'] == '801001') & (status['接口'] == 'SonPlate_Info')].iloc[0]
    assert son_row['结局'] == '失败'
    assert son_row['尝试次数'] == str(cb._RETRY_MAX)
    assert 'connection reset' in son_row['说明']

    skip_row = status[(status['板块代码'] == '801001') & (status['阶段'] == '成分股')].iloc[0]
    assert skip_row['结局'] == '跳过'
    assert '子板块' in skip_row['说明']

    summary = read_csv(csv_path(tmp_path, "fetch_summary"))
    s1 = summary[summary['板块代码'] == '801001'].iloc[0]
    assert s1['结局'] == '失败'
    assert s1['失败接口'] == 'SonPlate_Info'
    assert s1['成分股跳过数'] == '1'
    assert s1['成分股成功数'] == '0'


def test_empty_son_and_son_failure_coexist(tmp_path, monkeypatch):
    """同时存在空子板块与子板块失败的板块：前者回退成功，后者记失败并跳过成分股。"""
    pids = ["801001", "801002", "801003"]
    sons = {"801003": [["801103", "子C", "30"]]}
    api = FakeApi(build_routes(pids, sons, empty_son=["801001"]),
                  failing=fail_for("SonPlate_Info", "PlateID", "801002"))
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=4)

    sub = read_csv(csv_path(tmp_path, "sub_plate_info"))
    # 801001 回退自身；801002 无行；801003 正常子板块
    assert set(sub[sub['父代码'] == '801001']['子代码']) == {'801001'}
    assert '801002' not in set(sub['父代码'])
    assert set(sub[sub['父代码'] == '801003']['子代码']) == {'801103'}

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    r1 = status[(status['板块代码'] == '801001') & (status['接口'] == 'SonPlate_Info')].iloc[0]
    r2 = status[(status['板块代码'] == '801002') & (status['接口'] == 'SonPlate_Info')].iloc[0]
    assert (r1['结局'], r2['结局']) == ('成功', '失败')

    summary = read_csv(csv_path(tmp_path, "fetch_summary"))
    assert summary[summary['板块代码'] == '801001'].iloc[0]['结局'] == '成功'
    assert summary[summary['板块代码'] == '801002'].iloc[0]['结局'] == '失败'
    assert summary[summary['板块代码'] == '801003'].iloc[0]['结局'] == '成功'


def test_stock_list_failure_recorded_per_son_plate(tmp_path, monkeypatch):
    """某个子板块的成分股接口持续失败：定位到板块+子板块，其他子板块照常。"""
    pids = ["801001"]
    sons = {"801001": [["801101", "子A", "10"], ["801102", "子B", "20"]]}
    api = FakeApi(build_routes(pids, sons),
                  failing=fail_for("ZhiShuStockList_W8", "PlateID", "801102"))
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=4)

    stock = read_csv(csv_path(tmp_path, "stock_info"))
    assert set(stock['子代码']) == {'801101'}
    assert len(stock) == 2

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    rows = status[status['阶段'] == '成分股']
    ok = rows[rows['子板块代码'] == '801101'].iloc[0]
    bad = rows[rows['子板块代码'] == '801102'].iloc[0]
    assert ok['结局'] == '成功'
    assert ok['尝试次数'] == '1'
    assert bad['结局'] == '失败'
    assert bad['尝试次数'] == str(cb._RETRY_MAX)
    assert 'connection reset' in bad['说明']

    summary = read_csv(csv_path(tmp_path, "fetch_summary"))
    s = summary[summary['板块代码'] == '801001'].iloc[0]
    assert s['结局'] == '失败'
    assert (s['成分股成功数'], s['成分股失败数']) == ('1', '1')


def test_malformed_payload_is_failure_not_silent_empty(tmp_path, monkeypatch):
    pids = ["801001", "801002"]
    routes = build_routes(pids, {})
    routes["GetPlate_Info_QJ"] = json.dumps({"List": ["字段不足"]})
    install(monkeypatch, tmp_path, FakeApi(routes))

    cb.crawl_date(DATE, max_workers=4)

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    rows = status[status['接口'] == 'GetPlate_Info_QJ']
    assert set(rows['结局']) == {'失败'}
    assert '字段数不符' in rows.iloc[0]['说明']

    # 结构异常不影响其余数据产出
    main = read_csv(csv_path(tmp_path, "main_plate_info"))
    assert set(main['父代码']) == set(pids)
    assert set(main['排名']) == {''}

    summary = read_csv(csv_path(tmp_path, "fetch_summary"))
    assert set(summary['失败接口']) == {'GetPlate_Info_QJ'}


def test_ranking_first_page_failure_still_writes_outcome_files(tmp_path, monkeypatch):
    """板块列表首页就失败：业务 CSV 无法产出，但结局文件必须落盘说明原因。"""
    api = FakeApi({}, failing=lambda api, params: "timeout")
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=2)

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    row = status[status['接口'] == 'RealRankingInfo'].iloc[0]
    assert row['阶段'] == '板块列表'
    assert row['结局'] == '失败'
    assert row['尝试次数'] == str(cb._RETRY_MAX)
    assert 'timeout' in row['说明']
    assert csv_path(tmp_path, "fetch_summary").exists()
    assert not csv_path(tmp_path, "main_plate_info").exists()


def test_ranking_later_page_failure_keeps_acquired_plates(tmp_path, monkeypatch):
    """板块列表第二页失败：保留第一页板块继续抓取，并在结局里记录分页失败。"""
    first = [ranking_item("801001"), ranking_item("801002")]
    sons = {"801001": [["801101", "子A", "10"]], "801002": [["801102", "子B", "20"]]}
    routes = build_routes(["801001", "801002"], sons)
    routes["RealRankingInfo"] = json.dumps({"list": first})

    def _failing(api, params):
        return "page2 timeout" if api == "RealRankingInfo" and params.get("Index") == "30" else None

    install(monkeypatch, tmp_path, FakeApi(routes, failing=_failing))

    cb.crawl_date(DATE, max_workers=4)

    main = read_csv(csv_path(tmp_path, "main_plate_info"))
    assert set(main['父代码']) == {'801001', '801002'}
    stock = read_csv(csv_path(tmp_path, "stock_info"))
    assert len(stock) == 4

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    listing = status[status['阶段'] == '板块列表'].iloc[0]
    assert listing['结局'] == '失败'
    assert 'page2 timeout' in listing['说明']


def test_ambiguous_values_in_note_do_not_break_summary(tmp_path, monkeypatch):
    """失败原因里带逗号/换行等字符时，CSV 仍可被正常解析且能定位板块。"""
    pids = ["801001"]
    api = FakeApi(build_routes(pids, {}),
                  failing=lambda api, params: "502 bad gateway, upstream, timeout")
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=2)

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    assert len(status) > 0
    assert set(status['结局']) == {'失败'}
    assert all("502 bad gateway, upstream, timeout" in n for n in status['说明'])


def test_son_plate_failure_keeps_plate_in_main_csv(tmp_path, monkeypatch):
    """子板块失败只影响子板块/成分股，主表数据仍完整保留该板块。"""
    pids = ["801001", "801002"]
    api = FakeApi(build_routes(pids, {}),
                  failing=fail_for("SonPlate_Info", "PlateID", "801001"))
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=4)

    main = read_csv(csv_path(tmp_path, "main_plate_info"))
    assert set(main['父代码']) == set(pids)
    # 失败板块的 QJ / 盘口 / 爆发原因照常有值
    row = main[main['父代码'] == '801001'].iloc[0]
    assert row['排名'] != ''
    assert row['换手率'] != ''
    assert row['爆发原因'] == '测试原因'


def test_all_business_csvs_written_when_every_plate_fails(tmp_path, monkeypatch):
    """所有板块的板块数据接口都失败时，三个 CSV 仍按原结构产出（空表也行）。"""
    pids = ["801001", "801002"]
    api = FakeApi(build_routes(pids, {}),
                  failing=lambda api, params: "all down"
                  if api in ("GetPlate_Info_QJ", "GetPanKou",
                             "GetDayBaseFaceListZDEvnArt", "SonPlate_Info") else None)
    install(monkeypatch, tmp_path, api)

    cb.crawl_date(DATE, max_workers=4)

    main = read_csv(csv_path(tmp_path, "main_plate_info"))
    sub = read_csv(csv_path(tmp_path, "sub_plate_info"))
    stock = read_csv(csv_path(tmp_path, "stock_info"))
    assert list(main.columns) == MAIN_COLUMNS
    assert list(sub.columns) == SUB_COLUMNS
    assert list(stock.columns) == STOCK_COLUMNS
    assert set(main['父代码']) == set(pids)   # 排行数据仍能补出主表
    assert len(sub) == 0
    assert len(stock) == 0

    summary = read_csv(csv_path(tmp_path, "fetch_summary"))
    assert set(summary['结局']) == {'失败'}
    assert all(n.count(',') >= 3 for n in summary['失败接口'])


def test_do_request_returning_none_is_treated_as_failure(tmp_path, monkeypatch):
    """兼容以返回 None 表示重试耗尽的外部 do_request 实现（旧签名/测试桩）。"""
    pids = ["801001", "801002"]
    sons = {p: [[f"{p}9", f"子{p}", "5"]] for p in pids}
    api = FakeApi(build_routes(pids, sons))
    install(monkeypatch, tmp_path, api)

    real_do_request = cb.do_request

    def legacy_do_request(params, label="", api=None):
        if params.get("a") == "GetPanKou" and params.get("StockID") == "801002":
            return None  # 旧实现：重试耗尽返回 None
        return real_do_request(params, label, api=api)

    monkeypatch.setattr(cb, "do_request", legacy_do_request)

    cb.crawl_date(DATE, max_workers=4)

    main = read_csv(csv_path(tmp_path, "main_plate_info"))
    assert set(main['父代码']) == set(pids)
    assert main[main['父代码'] == '801002'].iloc[0]['换手率'] == ''

    status = read_csv(csv_path(tmp_path, "fetch_status"))
    row = status[(status['板块代码'] == '801002') & (status['接口'] == 'GetPanKou')].iloc[0]
    assert row['结局'] == '失败'
    assert row['尝试次数'] == str(cb._RETRY_MAX)


def test_business_csv_rows_order_and_content_stable_across_runs(tmp_path, monkeypatch):
    """同样的输入重复跑，三个业务 CSV 与结局文件内容完全一致（并行不影响可核对性）。"""
    pids = ["801001", "801002", "801003"]
    sons = {p: [[f"{p}9", f"子{p}", "5"], [f"{p}8", f"子{p}b", "6"]] for p in pids}

    snapshots = []
    for run in range(2):
        outdir = tmp_path / f"run{run}"
        outdir.mkdir()
        install(monkeypatch, outdir, FakeApi(build_routes(pids, sons)))
        cb.crawl_date(DATE, max_workers=4)
        snapshots.append({prefix: csv_path(outdir, prefix).read_bytes()
                          for prefix in ("main_plate_info", "sub_plate_info", "stock_info",
                                         "fetch_status", "fetch_summary")})

    for name in snapshots[0]:
        assert snapshots[0][name] == snapshots[1][name], f"{name} 内容不稳定"

    # 子板块与成分股按板块列表顺序输出（每个板块 2 个子板块 × 每个子板块 2 只）
    sub = read_csv(tmp_path / "run0" / f"sub_plate_info_{DATE}.csv")
    assert list(sub['父代码']) == ["801001", "801001", "801002", "801002", "801003", "801003"]
    stock = read_csv(tmp_path / "run0" / f"stock_info_{DATE}.csv")
    assert list(stock['父代码']) == ["801001"] * 4 + ["801002"] * 4 + ["801003"] * 4
    assert list(stock['子代码']) == ["8010019"] * 2 + ["8010018"] * 2 + \
        ["8010029"] * 2 + ["8010028"] * 2 + ["8010039"] * 2 + ["8010038"] * 2
