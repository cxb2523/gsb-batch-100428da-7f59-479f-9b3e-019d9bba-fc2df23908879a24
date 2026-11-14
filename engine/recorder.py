"""记录模块：本地 Excel（xlsx）/ JSONL 备份 / 飞书多维表。"""
import datetime
import json
import os

try:
    from openpyxl import Workbook, load_workbook
except ImportError:
    Workbook = None

from config import DATA_DIR, RECORDS_JSONL, RECORDS_XLSX
from engine.feishu import write_feishu


HEADERS = ["时间", "岗位", "候选人", "职位", "公司", "匹配分", "原因", "结果", "方式"]


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Recorder:
    def __init__(self, config, on_log=None):
        self.config = config
        self.on_log = on_log or (lambda *a: None)
        self._feishu_writer = None

    def log(self, msg):
        self.on_log(msg)

    def write(self, profile, score, reason, result, mode):
        os.makedirs(DATA_DIR, exist_ok=True)
        record = {
            "time": now_str(),
            "position_name": self.config.get("job", {}).get("position_name", ""),
            "name": profile.get("name", ""),
            "position": profile.get("position", ""),
            "company": profile.get("company", ""),
            "score": score,
            "reason": reason,
            "result": result,
            "mode": mode,
        }
        self._append_jsonl(record)

        rec = self.config.get("recording", {})
        if rec.get("mode") == "feishu":
            try:
                write_feishu(rec.get("feishu", {}), record)
                self.log(f"[飞书] 已写入：{record['name']}")
            except Exception as e:
                self.log(f"[飞书] 写入失败（已留在本地 JSONL）：{e}")
        else:
            self._append_xlsx(record)

        return record

    def _append_jsonl(self, record):
        with open(RECORDS_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _append_xlsx(self, record):
        if Workbook is None:
            self.log("[Excel] 缺少 openpyxl，仅保留 JSONL 记录")
            return
        if os.path.exists(RECORDS_XLSX):
            wb = load_workbook(RECORDS_XLSX)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "招聘记录"
            ws.append(HEADERS)
        ws.append([
            record["time"], record["position_name"], record["name"], record["position"],
            record["company"], record["score"], record["reason"], record["result"], record["mode"],
        ])
        wb.save(RECORDS_XLSX)
        self.log(f"[Excel] 已写入 data/招聘记录.xlsx：{record['name']}")


def export_xlsx():
    """把 JSONL 全量合并进 xlsx（兜底导出）。"""
    if Workbook is None or not os.path.exists(RECORDS_JSONL):
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "招聘记录"
    ws.append(HEADERS)
    with open(RECORDS_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ws.append([
                r.get("time", ""), r.get("position_name", ""), r.get("name", ""),
                r.get("position", ""), r.get("company", ""), r.get("score", ""),
                r.get("reason", ""), r.get("result", ""), r.get("mode", ""),
            ])
    wb.save(RECORDS_XLSX)
    return True
