"""采集麦当劳体验活动数据，生成 data/ 下的静态快照。

用法::

    export MCD_MCP_TOKEN="你的 Token"
    python src/collect.py                 # 全量采集城市 + 门店，抽样采集场次
    python src/collect.py --deep          # 额外深采：为每个城市首店取日期与场次
    python src/collect.py --limit-cities 20   # 只采前 20 个城市（快速试跑）

所有调用均为只读。写操作工具已在 mcp_client 中列入黑名单。

门店采集的健壮性保证：
  * 每个城市处理完立即原子写入 stores.json（含逐城成功/失败状态 cityStatus），
    任意时刻中断都不丢已完成城市；
  * --resume 只跳过上次 status=success 的城市，failed 或无记录的城市重新采集；
  * 单城采集失败只记录状态并继续后续城市，结束时打印成功/失败/跳过城市数。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_client import McDonaldsMcp, McpError  # noqa: E402
from toon import parse_toon  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CN_TZ = timezone(timedelta(hours=8))


def _dump(name: str, payload) -> str:
    path = os.path.join(DATA_DIR, name)
    _atomic_dump_json(path, payload)
    return path


def _atomic_dump_json(path: str, payload) -> None:
    """原子写 JSON：先写同目录临时文件再 os.replace 替换。

    长任务随时可能被中断（代理 502、进程被杀、Ctrl+C），直接覆盖目标文件
    可能留下半截 JSON，导致 --resume 时快照损坏只能整段重采。
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


def _load(name: str):
    """读取 data/ 下已存在的快照；不存在或损坏时返回 None。"""
    return _load_json(os.path.join(DATA_DIR, name))


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _now() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------
# 门店采集（逐城落盘 + 断点续采）
# ---------------------------------------------------------------------
# 城市状态取值：
#   success —— 上次调用成功（含门店数为 0 的合法返回），续采时跳过；
#   failed  —— 上次调用抛错或中途被打断后未留下成功记录，续采时重新采集。
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


def _city_status(code: str, name: str, status: str, stores=None, error: str = "") -> dict:
    entry = {
        "code": code,
        "name": name,
        "status": status,
        "storeCount": len(stores) if stores else 0,
        "updatedAt": _now(),
    }
    if error:
        entry["error"] = error
    return entry


def _write_stores_snapshot(path: str, spu_id, planned: list,
                           stores_by_city: dict, city_status: dict) -> None:
    """把当前进度原子写入 stores.json。

    每个城市处理完立即调用，保证任意时刻中断都不丢已完成城市。
    保留 byCity / totalStores / representativeSpuId 等既有字段，
    build_atlas.py 无需任何改动即可继续消费。
    """
    total_stores = sum(len(v) for v in stores_by_city.values())
    success_codes = {c["code"] for c in planned
                     if city_status.get(c["code"], {}).get("status") == STATUS_SUCCESS}
    doc = {
        "capturedAt": _now(),
        "representativeSpuId": spu_id,
        # totalStores 只统计成功城市里的门店（失败城市不进 byCity）
        "totalStores": total_stores,
        # 全部计划城市均成功才算完整快照，否则是可续采的部分快照
        "partial": bool(planned) and len(success_codes) < len(planned),
        "citiesDone": len(success_codes),
        "citiesTotal": len(planned),
        "byCity": stores_by_city,
        # 新增：逐城采集状态。失败/未采城市也会留下 failed 记录，
        # 续采据此与 byCity 的键共同判断，而不是只看 byCity 是否出现过。
        "cityStatus": city_status,
    }
    _atomic_dump_json(path, doc)


def collect_city_stores(client, all_cities: list, spu: dict, data_dir: str,
                        resume: bool = False) -> dict:
    """逐城采集门店，每城处理完立即落盘，返回统计与结果。

    设计要点：
      * 单城失败（McpError 或其它异常）只记录 failed 状态并继续后面的城市，
        不中止整体任务；
      * --resume 只跳过上次真正 success 的城市；failed / 无记录 / 旧格式
        快照里的城市一律重新采集；
      * 每次状态变更都原子重写 stores.json，中断重启后进度与本函数返回值一致。

    返回::

        {"byCity": {...}, "cityStatus": {...},
         "succeeded": n, "failed": n, "skipped": n, "totalStores": n}
    """
    os.makedirs(data_dir, exist_ok=True)
    stores_path = os.path.join(data_dir, "stores.json")

    stores_by_city: dict = {}
    city_status: dict = {}
    if resume:
        prev = _load_json(stores_path) or {}
        # 旧版快照没有 cityStatus：byCity 里的键都是当时成功的城市，
        # 迁移为 success 状态，保持老快照的续采语义不回退。
        prev_status = prev.get("cityStatus")
        if isinstance(prev_status, dict) and prev_status:
            city_status.update(prev_status)
        for code, stores in (prev.get("byCity") or {}).items():
            stores_by_city[code] = stores
            if code not in city_status:
                city_status[code] = _city_status(
                    code, code, STATUS_SUCCESS, stores
                )

    planned_codes = [c["code"] for c in all_cities]
    skipped = sum(
        1 for code in planned_codes
        if city_status.get(code, {}).get("status") == STATUS_SUCCESS
    )

    succeeded = 0
    failed = 0
    spu_id = spu["spuId"]
    for idx, city in enumerate(all_cities, 1):
        code = city["code"]
        if city_status.get(code, {}).get("status") == STATUS_SUCCESS:
            # 续采：上次真正成功的城市才跳过
            continue
        try:
            stores = client.party_stores(spu_id, city)
        except McpError as exc:
            # 网络类失败（代理 502 / 429 等）不中止整体采集，
            # 记录失败原因并继续，续采时该城市会被重新采集
            failed += 1
            city_status[code] = _city_status(
                code, city["name"], STATUS_FAILED, error=str(exc)
            )
            print("    query-party-store(%s) 失败：%s" % (city["name"], exc))
            _write_stores_snapshot(
                stores_path, spu_id, all_cities, stores_by_city, city_status
            )
            continue
        except Exception as exc:  # noqa: BLE001 - 单城隔离，绝不让整任务崩掉
            failed += 1
            city_status[code] = _city_status(
                code, city["name"], STATUS_FAILED,
                error="%s: %s" % (type(exc).__name__, exc),
            )
            print("    query-party-store(%s) 异常：%s" % (city["name"], exc))
            _write_stores_snapshot(
                stores_path, spu_id, all_cities, stores_by_city, city_status
            )
            continue

        succeeded += 1
        stores_by_city[code] = stores
        city_status[code] = _city_status(code, city["name"], STATUS_SUCCESS, stores)
        # 每个城市处理完立即落盘：最后不足 25 个城市的进度也不会丢
        _write_stores_snapshot(
            stores_path, spu_id, all_cities, stores_by_city, city_status
        )
        if idx % 25 == 0 or idx == len(all_cities):
            print(
                "    ...%d/%d 城，成功 %d，失败 %d，跳过 %d，累计 %d 家门店"
                % (
                    idx,
                    len(all_cities),
                    succeeded,
                    failed,
                    skipped,
                    sum(len(v) for v in stores_by_city.values()),
                )
            )

    # 收尾再写一次：全部城市都被跳过时也要把迁移后的 cityStatus 落盘
    _write_stores_snapshot(
        stores_path, spu_id, all_cities, stores_by_city, city_status
    )
    return {
        "byCity": stores_by_city,
        "cityStatus": city_status,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "totalStores": sum(len(v) for v in stores_by_city.values()),
    }


def collect(client: McDonaldsMcp, args) -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    stats = {"tools_listed": 0, "spus": 0, "cities": 0, "stores": 0, "sessions": 0}

    # ---- 0. 工具清单（用于核对工具数与名称） -------------------------
    tools = client.tools_list()
    stats["tools_listed"] = len(tools)
    _dump(
        "tools_snapshot.json",
        {
            "capturedAt": _now(),
            "count": len(tools),
            "tools": tools,
        },
    )
    print("[0/7] tools/list → %d 个工具" % len(tools))

    # ---- 1. 积分商城 SPU（体验活动入口） -----------------------------
    spus = client.experience_spus()
    stats["spus"] = len(spus)
    _dump("experiences.json", {"capturedAt": _now(), "items": spus})
    print("[1/7] mall-points-products → %d 个 SPU" % len(spus))

    # 体验类 = shopId 5（party-order-create 仅支持 shopId=5）
    experience_spus = [s for s in spus if s.get("shopId") == 5]
    if not experience_spus:
        print("    未找到 shopId=5 的体验类 SPU，回退使用全部 SPU")
        experience_spus = spus

    # ---- 2. 各体验品类的可预约城市 ----------------------------------
    city_map: dict = {}
    coverage = []
    for spu in experience_spus:
        spu_id = spu["spuId"]
        try:
            cities = client.party_cities(spu_id)
        except McpError as exc:
            print("    query-party-city(spuId=%s) 失败：%s" % (spu_id, exc))
            continue
        coverage.append(
            {
                "catName": spu.get("catName"),
                "spuId": spu_id,
                "spuName": spu.get("spuName"),
                "cities": len(cities),
                "cityCodes": [str(c["code"]) for c in cities],
            }
        )
        for city in cities:
            key = str(city["code"])
            if key not in city_map:
                city_map[key] = {
                    "code": key,
                    "name": city["name"],
                    "initial": city.get("initial"),
                    "latitude": city["latitude"],
                    "longitude": city["longitude"],
                    "sortNo": city.get("sortNo"),
                }
        time.sleep(0.2)
    stats["cities"] = len(city_map)
    _dump(
        "cities.json",
        {
            "capturedAt": _now(),
            "count": len(city_map),
            "categoryCoverage": coverage,
            "cities": sorted(city_map.values(), key=lambda c: c["sortNo"] or 0),
        },
    )
    print(
        "[2/7] 体验品类城市覆盖 %s → 去重后 %d 城"
        % (
            {c["catName"]: c["cities"] for c in coverage},
            len(city_map),
        )
    )

    # ---- 3. 每个城市的可办活动门店（逐城落盘，可断点续采） ----------
    all_cities = sorted(city_map.values(), key=lambda c: c["sortNo"] or 0)
    if args.limit_cities:
        all_cities = all_cities[: args.limit_cities]

    if args.resume:
        print("    续采：仅跳过上次真正成功的城市，失败/未采城市重新采集")
    store_result = collect_city_stores(
        client, all_cities, experience_spus[0], DATA_DIR, resume=args.resume
    )
    stores_by_city = store_result["byCity"]
    total_stores = store_result["totalStores"]
    stats["stores"] = total_stores
    print(
        "[3/7] 门店采集完成 → 成功 %d 城，失败 %d 城，跳过 %d 城，%d 家门店"
        % (
            store_result["succeeded"],
            store_result["failed"],
            store_result["skipped"],
            total_stores,
        )
    )

    # ---- 4. 场次抽样（日期 + 场次） ---------------------------------
    sessions = []
    sample = all_cities if args.deep else all_cities[: args.sample_cities]
    for city in sample:
        stores = stores_by_city.get(city["code"]) or []
        if not stores:
            continue
        store_code = stores[0].get("code") or stores[0].get("storeCode")
        try:
            dates = client.party_dates(experience_spus[0]["spuId"], store_code)
        except McpError:
            continue
        if not dates:
            continue
        first = dates[0].get("date")
        try:
            day_sessions = client.party_sessions(
                experience_spus[0]["spuId"], store_code, first
            )
        except McpError:
            day_sessions = []
        sessions.append(
            {
                "cityCode": city["code"],
                "cityName": city["name"],
                "storeCode": store_code,
                "storeName": stores[0].get("name"),
                "dates": dates,
                "sessions": day_sessions,
            }
        )
        stats["sessions"] += len(day_sessions)
    _dump(
        "sessions.json",
        {
            "capturedAt": _now(),
            "spuId": experience_spus[0]["spuId"],
            "sampledCities": len(sessions),
            "totalSessions": stats["sessions"],
            "items": sessions,
        },
    )
    print("[4/7] 场次抽样 → %d 城 / %d 个场次" % (len(sessions), stats["sessions"]))

    # ---- 5. 营养表（TOON 格式，解析后落盘） -------------------------
    try:
        raw = client.call("list-nutrition-foods")
        nutrition_text = raw.get("data")
        if isinstance(nutrition_text, str):
            items = parse_toon(nutrition_text)
            _dump(
                "nutrition.json",
                {
                    "capturedAt": _now(),
                    "source": "list-nutrition-foods",
                    "count": len(items),
                    "items": items,
                },
            )
            print("[5/7] list-nutrition-foods → %d 项（TOON 已解析）" % len(items))
        else:
            print("[5/7] list-nutrition-foods 返回非预期类型：%s" % type(nutrition_text))
    except McpError as exc:
        print("[5/7] list-nutrition-foods 失败：%s" % exc)

    # ---- 6. 抽奖活动信息 --------------------------------------------
    try:
        lottery = client.call_data("query-lottery-info") or {}
        _dump("lottery.json", {"capturedAt": _now(), "data": lottery})
        prizes = lottery.get("prizes", [])
        print(
            "[6/7] query-lottery-info → %s，%d 项奖品，单次 %s 积分"
            % (lottery.get("activityName"), len(prizes), lottery.get("drawPoint"))
        )
    except McpError as exc:
        print("[6/7] query-lottery-info 失败：%s" % exc)

    # ---- 7. 营销活动日历 --------------------------------------------
    try:
        calendar = client.call_data("campaign-calendar") or {}
        daily = calendar.get("dailyList") or []
        events = [e for d in daily for e in (d.get("events") or [])]
        _dump(
            "campaign_calendar.json",
            {
                "capturedAt": _now(),
                "days": len(daily),
                "events": len(events),
                "data": calendar,
            },
        )
        print("[7/7] campaign-calendar → %d 天 / %d 个事件" % (len(daily), len(events)))
    except McpError as exc:
        print("[7/7] campaign-calendar 失败：%s" % exc)

    print("\n采集完成：%s" % json.dumps(stats, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="麦当劳体验活动数据采集")
    parser.add_argument("--deep", action="store_true", help="为每个城市首店深采日期与场次")
    parser.add_argument("--sample-cities", type=int, default=40, help="场次抽样的城市数")
    parser.add_argument("--limit-cities", type=int, default=0, help="只采集前 N 个城市")
    parser.add_argument("--resume", action="store_true",
                        help="断点续采：只跳过 stores.json 中上次真正成功的城市，"
                             "失败或未采的城市重新采集")
    args = parser.parse_args()

    try:
        client = McDonaldsMcp()
    except McpError as exc:
        print("错误：%s" % exc)
        return 1
    return collect(client, args)


if __name__ == "__main__":
    raise SystemExit(main())
