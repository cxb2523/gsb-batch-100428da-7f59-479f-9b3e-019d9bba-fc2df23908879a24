"""启动参数配置：读写 data/config.json，提供默认值。"""
import copy
import json
import os
import sys
import threading

if getattr(sys, "frozen", False):
    # 打包后：数据（配置/登录态/记录）保存在 exe 同目录，升级不丢数据
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
RECORDS_XLSX = os.path.join(DATA_DIR, "招聘记录.xlsx")
RECORDS_JSONL = os.path.join(DATA_DIR, "records.jsonl")
# 干净 Profile：首次启动后请在弹出的 Edge 窗口手动扫码/密码登录自己的 BOSS 直聘账号，
# 登录态会保存在该目录，后续运行自动复用。原 edge_profile（原作者机器加密，本机不可用）保留不动。
EDGE_PROFILE_DIR = os.path.join(DATA_DIR, "edge_profile_clean")

# 系统 Edge 路径（找不到则回退 playwright 默认 Chromium）
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

DEFAULT_CONFIG = {
    "job": {
        "position_name": "",        # 岗位名称（必填，用于切换推荐岗位）
        "new_talent_flow": False,   # 是否走「新牛人」子流程
    },
    "filters": {
        "gender": "all",            # all / male / female
        "min_age": 20,
        "max_age": 45,
        "education": [],            # 多选学历：大专/本科/硕士/博士
        "experience": [],           # 多选经验：1-3年/3-5年/5-10年/10年以上
        "activity": [],             # 多选活跃度：刚刚活跃/今日活跃/本周活跃
        "schools": [],              # 多选院校：985/211/双一流
        "job_intention": [],        # 多选求职意向：只看在线/只看今日查看
        "job_hopping": [],          # 多选跳槽频率：稳定/一般/频繁
        "keywords": [],             # 技能关键词（匹配打分）
    },
    "matching": {
        "mode": "rules",            # rules / ai
        "min_score": 60,
        "rules": {
            "education_weight": 20,
            "work_years_weight": 20,
            "age_weight": 10,
            "gender_weight": 10,
            "keyword_weight": 40,
            "keywords": [],
        },
        "ai": {
            "api_url": "",
            "api_key": "",
            "model": "gpt-4o-mini",
            "fallback_to_rules": True,
            "system_prompt": "你是资深招聘专家。根据岗位画像和候选人简历信息，输出 JSON：{\"score\": 0-100, \"reason\": \"简短理由\"}。",
        },
    },
    "action": {
        # manual=手动辅助（自己点按钮执行）/ chat_greet=沟通页自动发常用语+求简历 / recommend=推荐牛人自动打招呼（需已发布岗位）
        "flow_mode": "manual",
        "greet_mode": "auto",       # auto（真实鼠标事件）/ manual（人工确认）
        "greet_message": "您好，看到您的简历与我们的岗位匹配度很高，方便进一步沟通吗？",
        "max_greet_count": 30,      # 单次运行最大主动沟通人数
        "interval_min_ms": 60000,   # 每人间隔 1 分钟
        "interval_max_ms": 60000,
        "auto_page": True,
        "max_pages": 5,
    },
    "recording": {
        "mode": "excel",            # excel / feishu
        "feishu": {
            "app_id": "",
            "app_secret": "",
            "app_token": "",
            "table_id": "",
        },
    },
}


def find_edge():
    for p in EDGE_PATHS:
        if os.path.exists(p):
            return p
    return None


def load_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg = _deep_merge(cfg, saved)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _deep_merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
