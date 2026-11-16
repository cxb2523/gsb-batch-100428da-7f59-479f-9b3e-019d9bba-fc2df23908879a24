"""门店采集逐城落盘与断点续采的离线验证。

运行::

    python -I tests/test_resume_collect.py

本测试**不发任何真实请求、不依赖 Token 与网络**：用本地伪造的 MCP 客户端
（FakeMcp）顶替 McDonaldsMcp，在临时目录里演练 stores.json 的写入与续采。

覆盖的验收点：
  1. 中途抛错（模拟进程被打断）后，已完成城市已逐城落盘，重启 --resume
     只补采失败/未采城市；
  2. 单城采集失败不中止整体任务，失败城市留下 failed 状态，续采重新采集；
  3. 最终门店总数与「全量一次成功」时完全一致，totalStores 只统计成功城市；
  4. stores.json 保留 byCity / totalStores / representativeSpuId 等既有字段，
     build_atlas.build() 无需改动即可继续打包；
  5. 旧格式快照（无 cityStatus）能平滑迁移，且不会把失败城市误判为已完成。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import collect  # noqa: E402
from mcp_client import McpError  # noqa: E402

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(
        name if condition else "%s %s" % (name, detail)
    )


# ----------------------------------------------------------------- 伪造客户端
class FakeMcp:
    """本地伪造的 MCP 客户端，只实现门店采集链路用到的接口。

    fail_codes: 每次调用都抛 McpError 的城市（模拟代理 502 / 限流）；
    fail_once:  仅第一次调用抛错、第二次成功的城市（模拟临时性故障）；
    crash_after: 处理完第 N 个城市后抛 AbortRun（模拟进程被中途杀死）。
    """

    def __init__(self, cities, fail_codes=None, fail_once=None, crash_after=None):
        self._cities = cities
        self.fail_codes = set(fail_codes or [])
        self.fail_once = dict(fail_once or {})  # code -> 已失败次数
        self.crash_after = crash_after
        self.calls = []  # 按调用顺序记录 (code, attempt_no)

    def party_stores(self, spu_id, city):
        code = city["code"]
        attempt = sum(1 for c, _ in self.calls if c == code) + 1
        self.calls.append((code, attempt))

        # 模拟进程在指定城市处理时被打断：抛 BaseException，不被采集循环吞掉
        if self.crash_after is not None and len(self.calls) == self.crash_after + 1:
            raise AbortRun("模拟采集中途进程被杀死（code=%s）" % code)

        if code in self.fail_codes:
            raise McpError("模拟代理 502（code=%s）" % code, http_status=502)

        if code in self.fail_once:
            if self.fail_once[code] > 0:
                self.fail_once[code] -= 1
                raise McpError("模拟临时限流 429（code=%s）" % code, code="429")

        # 每个城市 3 家门店，门店编码带城市前缀，方便核对总数与归属
        return [
            {"code": "%s-S%d" % (code, i), "name": "%s 门店%d" % (city["name"], i),
             "address": "测试路 %d 号" % i}
            for i in range(1, 4)
        ]


class AbortRun(BaseException):
    """模拟外部中断（Ctrl+C / kill），采集循环不应捕获它。"""


# ------------------------------------------------------------------- 测试工具
SPU = {"spuId": 1830, "spuName": "生日会"}


def make_cities(n):
    return [
        {"code": "1%05d" % i, "name": "测试市%02d" % i,
         "latitude": 30.0 + i, "longitude": 120.0 - i, "sortNo": i}
        for i in range(n)
    ]


def run_collect(cities, fake, data_dir, resume):
    """调用真实的 collect.collect_city_stores，数据落在临时目录。"""
    return collect.collect_city_stores(
        fake, cities, SPU, data_dir, resume=resume
    )


def load_stores(data_dir):
    with open(os.path.join(data_dir, "stores.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def expect_abort(fn):
    try:
        fn()
    except AbortRun:
        return True
    return False


# --------------------------------------------------------------------- 用例
def test_crash_then_resume(tmp):
    """中途抛错后重启续采：只补采未成功城市，最终总数与全量成功一致。"""
    cities = make_cities(10)
    data_dir = os.path.join(tmp, "crash")
    os.makedirs(data_dir)

    # 第一次：处理完前 4 个城市（调用序号 1..4）后，在第 5 个城市处被杀死
    fake1 = FakeMcp(cities, crash_after=4)
    aborted = expect_abort(lambda: run_collect(cities, fake1, data_dir, False))
    check("首次运行在第 5 城处被中断", aborted)

    doc = load_stores(data_dir)
    check("中断后 stores.json 仍可正常解析", isinstance(doc, dict))
    check("中断后已逐城落盘 4 个成功城市", len(doc["byCity"]) == 4,
          "实际 %d" % len(doc["byCity"]))
    check("中断快照 partial=True", doc.get("partial") is True)
    check("中断快照 citiesDone=4", doc.get("citiesDone") == 4,
          "实际 %s" % doc.get("citiesDone"))
    check("totalStores 只含成功城市（4*3=12）", doc.get("totalStores") == 12,
          "实际 %s" % doc.get("totalStores"))
    check("每个成功城市都有 cityStatus=success",
          all(doc["cityStatus"][c]["status"] == "success" for c in doc["byCity"]))
    check("被打断的第 5 城没有成功记录",
          cities[4]["code"] not in doc["byCity"])

    # 第二次：--resume，用一个完全健康的客户端补采
    fake2 = FakeMcp(cities)
    result = run_collect(cities, fake2, data_dir, True)

    check("续采跳过 4 个上次成功城市", result["skipped"] == 4,
          "实际 %d" % result["skipped"])
    check("续采新成功 6 个城市", result["succeeded"] == 6,
          "实际 %d" % result["succeeded"])
    check("续采无失败城市", result["failed"] == 0, "实际 %d" % result["failed"])
    # 关键：续采客户端只被调用了 6 次，成功城市没有被重复采集
    check("续采只调用了 6 次 party_stores", len(fake2.calls) == 6,
          "实际 %d" % len(fake2.calls))
    resumed_codes = {c for c, _ in fake2.calls}
    check("续采调用的正是上次未成功的 6 城",
          resumed_codes == {c["code"] for c in cities[4:]})

    doc2 = load_stores(data_dir)
    check("续采后 10 城全部在 byCity", len(doc2["byCity"]) == 10,
          "实际 %d" % len(doc2["byCity"]))
    check("续采后 totalStores=30，与全量成功一致", doc2["totalStores"] == 30,
          "实际 %s" % doc2["totalStores"])
    check("完整快照 partial=False", doc2.get("partial") is False)
    check("完整快照 citiesDone=10/10",
          doc2.get("citiesDone") == 10 and doc2.get("citiesTotal") == 10)
    check("representativeSpuId 保留", doc2.get("representativeSpuId") == 1830)
    check("10 城 cityStatus 全为 success",
          all(s["status"] == "success" for s in doc2["cityStatus"].values()))


def test_failed_city_retried_on_resume(tmp):
    """单城失败不中止任务；失败城市续采时重新采集，最终与全量成功一致。"""
    cities = make_cities(8)
    data_dir = os.path.join(tmp, "fail")
    os.makedirs(data_dir)

    # 城市 3 持续失败、城市 6 临时失败一次；其余城市成功
    fake1 = FakeMcp(cities, fail_codes={cities[2]["code"]},
                    fail_once={cities[5]["code"]: 1})
    result1 = run_collect(cities, fake1, data_dir, False)

    check("首轮成功 6 城", result1["succeeded"] == 6, "实际 %d" % result1["succeeded"])
    check("首轮失败 2 城且任务未中止", result1["failed"] == 2,
          "实际 %d" % result1["failed"])
    check("首轮跳过 0 城", result1["skipped"] == 0)
    check("首轮 totalStores 只算成功城市（6*3=18）",
          result1["totalStores"] == 18, "实际 %d" % result1["totalStores"])

    doc = load_stores(data_dir)
    check("失败城市不进 byCity",
          cities[2]["code"] not in doc["byCity"]
          and cities[5]["code"] not in doc["byCity"])
    check("失败城市留下 failed 状态与错误信息",
          doc["cityStatus"][cities[2]["code"]]["status"] == "failed"
          and doc["cityStatus"][cities[5]["code"]]["status"] == "failed"
          and bool(doc["cityStatus"][cities[2]["code"]].get("error")))
    check("有失败城市时 partial=True", doc.get("partial") is True)
    check("失败快照 totalStores 仍只含成功门店", doc.get("totalStores") == 18)

    # 续采：两个故障都解除
    fake2 = FakeMcp(cities)
    result2 = run_collect(cities, fake2, data_dir, True)
    check("续采跳过 6 个成功城市", result2["skipped"] == 6,
          "实际 %d" % result2["skipped"])
    check("续采补采 2 个失败城市", result2["succeeded"] == 2
          and result2["failed"] == 0)
    check("续采只调用 2 次 party_stores", len(fake2.calls) == 2,
          "实际 %d" % len(fake2.calls))

    doc2 = load_stores(data_dir)
    check("续采后 8 城全成功", len(doc2["byCity"]) == 8)
    check("续采后 totalStores=24，与全量成功一致", doc2["totalStores"] == 24,
          "实际 %s" % doc2["totalStores"])
    check("补采成功后状态从 failed 变 success",
          doc2["cityStatus"][cities[2]["code"]]["status"] == "success"
          and doc2["cityStatus"][cities[5]["code"]]["status"] == "success")
    check("补采后 partial=False", doc2.get("partial") is False)

    # 再次续采：全部成功，应当全部跳过、零调用
    fake3 = FakeMcp(cities)
    result3 = run_collect(cities, fake3, data_dir, True)
    check("全成功后再次续采：跳过 8 城、零调用",
          result3["skipped"] == 8 and len(fake3.calls) == 0)


def test_legacy_snapshot_migration(tmp):
    """旧格式 stores.json（无 cityStatus）：byCity 键迁移为 success。"""
    cities = make_cities(5)
    data_dir = os.path.join(tmp, "legacy")
    os.makedirs(data_dir)
    legacy_stores = {
        cities[0]["code"]: [{"code": "old-1", "name": "老店"}],
        cities[1]["code"]: [{"code": "old-2", "name": "老店2"}],
    }
    with open(os.path.join(data_dir, "stores.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "capturedAt": "2026-10-13 22:38:11",
            "representativeSpuId": 1830,
            "totalStores": 2,
            "partial": False,
            "citiesDone": 2,
            "citiesTotal": 2,
            "byCity": legacy_stores,
        }, fh, ensure_ascii=False)

    fake = FakeMcp(cities)
    result = run_collect(cities, fake, data_dir, True)
    check("旧快照两城被识别为成功并跳过", result["skipped"] == 2,
          "实际 %d" % result["skipped"])
    check("旧快照外的 3 城重新采集", result["succeeded"] == 3
          and len(fake.calls) == 3)
    doc = load_stores(data_dir)
    check("迁移后补齐 cityStatus 且旧两城为 success",
          all(doc["cityStatus"][c]["status"] == "success"
              for c in legacy_stores))
    check("旧门店数据原样保留",
          doc["byCity"][cities[0]["code"]] == legacy_stores[cities[0]["code"]])
    # 旧两城各 1 家 + 新城各 3 家 = 2 + 9 = 11
    check("迁移后续采 totalStores=11", doc["totalStores"] == 11,
          "实际 %s" % doc["totalStores"])


def test_no_resume_ignores_previous(tmp):
    """不带 --resume 时不跳过任何城市，全部重新采集并覆盖旧状态。"""
    cities = make_cities(4)
    data_dir = os.path.join(tmp, "fresh")
    os.makedirs(data_dir)

    fake1 = FakeMcp(cities, fail_codes={cities[0]["code"]})
    run_collect(cities, fake1, data_dir, False)

    fake2 = FakeMcp(cities)  # 健康客户端
    result = run_collect(cities, fake2, data_dir, False)
    check("非续采模式跳过数为 0", result["skipped"] == 0)
    check("非续采模式 4 城全部重新调用", len(fake2.calls) == 4,
          "实际 %d" % len(fake2.calls))
    check("非续采模式全部成功", result["succeeded"] == 4 and result["failed"] == 0)
    doc = load_stores(data_dir)
    check("非续采后 totalStores=12", doc["totalStores"] == 12)


def test_every_city_persisted_immediately(tmp):
    """每处理完一个城市，磁盘上的 stores.json 立刻反映该城市结果。"""
    cities = make_cities(6)
    data_dir = os.path.join(tmp, "immediate")
    os.makedirs(data_dir)

    seen_on_disk = []
    totals_on_disk = []
    fake = FakeMcp(cities)
    orig_write = collect._write_stores_snapshot
    ordered_codes = [c["code"] for c in cities]
    write_idx = {"i": 0}

    # 在每次真实落盘之后读取磁盘：验证「城市处理完即落盘」而不是攒批落盘
    def spying_write(path, spu_id, planned, by_city, city_status):
        orig_write(path, spu_id, planned, by_city, city_status)
        doc = load_stores(data_dir)
        if write_idx["i"] >= len(ordered_codes):
            return  # 循环结束后的收尾落盘，不属于「每城一次」的校验范围
        code = ordered_codes[write_idx["i"]]
        write_idx["i"] += 1
        seen_on_disk.append((code, code in doc["byCity"],
                             doc["cityStatus"][code]["status"]))
        totals_on_disk.append(doc["totalStores"])

    collect._write_stores_snapshot = spying_write
    try:
        result = run_collect(cities, fake, data_dir, False)
    finally:
        collect._write_stores_snapshot = orig_write
    check("6 个城市每次落盘都包含刚处理完的城市",
          len(seen_on_disk) == 6 and all(on_disk and st == "success"
                                         for _, on_disk, st in seen_on_disk),
          str(seen_on_disk))
    check("落盘门店数随城市逐个递增（3,6,9,12,15,18）",
          totals_on_disk == [3, 6, 9, 12, 15, 18], str(totals_on_disk))
    check("最终 totalStores=18", result["totalStores"] == 18)

    # 再用失败路径验证：失败城市也会立即留下 failed 记录
    data_dir2 = os.path.join(tmp, "immediate_fail")
    os.makedirs(data_dir2)
    fail_code = cities[2]["code"]
    fake_fail = FakeMcp(cities, fail_codes={fail_code})
    run_collect(cities, fake_fail, data_dir2, False)
    doc = load_stores(data_dir2)
    check("失败城市也立即落盘为 failed",
          doc["cityStatus"][fail_code]["status"] == "failed"
          and fail_code not in doc["byCity"])
    check("失败城市不计入 totalStores", doc["totalStores"] == 15)


def test_build_atlas_unchanged(tmp):
    """新字段不得破坏 build_atlas 打包流程；storeCount 只来自成功城市。"""
    import build_atlas

    cities = make_cities(5)
    data_dir = os.path.join(tmp, "atlas")
    os.makedirs(data_dir)
    fake = FakeMcp(cities, fail_codes={cities[1]["code"]})
    run_collect(cities, fake, data_dir, False)

    # build_atlas 通过模块级 DATA_DIR 找数据，重定向到临时目录
    old_data_dir = build_atlas.DATA_DIR
    build_atlas.DATA_DIR = data_dir
    try:
        # 提供最小 cities.json，其余输入缺省时 build() 有兜底
        with open(os.path.join(data_dir, "cities.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"capturedAt": "2026-11-16 10:00:00",
                       "cities": cities, "categoryCoverage": []}, fh,
                      ensure_ascii=False)
        atlas = build_atlas.build()
    finally:
        build_atlas.DATA_DIR = old_data_dir

    check("build_atlas 正常产出 atlas", isinstance(atlas, dict))
    # 4 个成功城市 * 3 家 = 12；失败城市不在 storeIndex
    check("atlas.storeCount 只统计成功城市门店（12）",
          atlas["meta"]["storeCount"] == 12,
          "实际 %s" % atlas["meta"]["storeCount"])
    check("失败城市不在 storeIndex",
          cities[1]["code"] not in atlas["storeIndex"])
    check("4 个成功城市都在 storeIndex",
          all(c["code"] in atlas["storeIndex"] for c in cities
              if c["code"] != cities[1]["code"]))
    check("representativeSpuId 透传到 atlas meta",
          atlas["meta"]["representativeSpuId"] == 1830)
    check("storeIndex 每店保留 code/name",
          all(s.get("code") and s.get("name")
              for stores in atlas["storeIndex"].values() for s in stores))


def test_total_matches_full_success(tmp):
    """总验收：故障 + 中断 + 续采后的门店总数 == 全量一次成功的门店总数。"""
    cities = make_cities(12)

    # 基准：全量一次成功
    base_dir = os.path.join(tmp, "base")
    os.makedirs(base_dir)
    base = run_collect(cities, FakeMcp(cities), base_dir, False)

    # 故障路径：先持续失败 2 城 + 中途被杀死，再续采，最后故障解除再续采
    work_dir = os.path.join(tmp, "work")
    os.makedirs(work_dir)
    fail_codes = {cities[3]["code"], cities[9]["code"]}
    fake1 = FakeMcp(cities, fail_codes=fail_codes, crash_after=5)
    expect_abort(lambda: run_collect(cities, fake1, work_dir, False))

    fake2 = FakeMcp(cities, fail_codes=fail_codes)
    mid = run_collect(cities, fake2, work_dir, True)
    check("中断后续采仍有 2 个持续失败城市", mid["failed"] == 2)

    fake3 = FakeMcp(cities)  # 故障全部解除
    final = run_collect(cities, fake3, work_dir, True)
    check("最终续采失败清零", final["failed"] == 0)

    check("最终门店总数与全量成功完全一致",
          final["totalStores"] == base["totalStores"] == 36,
          "故障路径 %s vs 基准 %s" % (final["totalStores"], base["totalStores"]))
    check("最终 byCity 城市数与全量成功一致",
          len(final["byCity"]) == len(base["byCity"]) == 12)
    check("最终每城门店列表与基准逐城相等",
          all(final["byCity"][c["code"]] == base["byCity"][c["code"]]
              for c in cities))
    final_doc = load_stores(work_dir)
    check("最终快照字段完整且 partial=False",
          final_doc["partial"] is False
          and final_doc["citiesDone"] == 12
          and final_doc["citiesTotal"] == 12
          and set(final_doc.keys()) >=
          {"byCity", "totalStores", "representativeSpuId", "cityStatus"})


def main():
    tmp = tempfile.mkdtemp(prefix="mcd_resume_test_")
    try:
        for fn in (
            test_crash_then_resume,
            test_failed_city_retried_on_resume,
            test_legacy_snapshot_migration,
            test_no_resume_ignores_previous,
            test_every_city_persisted_immediately,
            test_build_atlas_unchanged,
            test_total_matches_full_success,
        ):
            fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  [FAIL] " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
