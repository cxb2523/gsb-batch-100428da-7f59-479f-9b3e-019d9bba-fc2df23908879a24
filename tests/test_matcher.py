"""匹配引擎单元测试。运行：python tests/test_matcher.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine.matcher import extract_work_years, match, score_by_rules


failures = 0


def run(name, profile, config, expect_pass):
    global failures
    score, passed, reasons = score_by_rules(profile, config)
    ok = passed is expect_pass
    print(f"{'PASS' if ok else 'FAIL'} | {name} | score={score} passed={passed} | {'；'.join(reasons)}")
    if not ok:
        failures += 1


base = {
    "filters": {
        "gender": "male",
        "min_age": 25,
        "max_age": 40,
        "education": ["本科", "硕士"],
        "experience": ["3-5年"],
        "schools": [],
        "keywords": ["Java", "Spring"],
    },
    "matching": {
        "mode": "rules",
        "min_score": 60,
        "rules": {
            "education_weight": 20,
            "work_years_weight": 20,
            "age_weight": 10,
            "gender_weight": 10,
            "keyword_weight": 40,
            "keywords": ["Java", "Spring"],
        },
    },
}

full = {"name": "张三", "age": 30, "gender": "男", "education": "本科", "work_years": 5,
        "position": "Java 后端", "company": "某公司", "tags": ["Java", "Spring Cloud"], "summary": "5年后端经验"}

run("完全匹配应通过", dict(full), base, True)
run("学历不符应拒绝", {**full, "education": "大专"}, base, False)
run("经验不符应拒绝", {**full, "work_years": 1}, base, False)
run("关键词全不命中应拒绝", {**full, "position": "Python 开发", "tags": ["Python"]}, base, False)
run("性别不符应拒绝", {**full, "gender": "女"}, base, False)
run("年龄超界应拒绝", {**full, "age": 45}, base, False)
run("院校要求不影响规则打分（由页面筛选处理）", {**full, "school": "普通大学"},
    {**base, "filters": {**base["filters"], "schools": ["985", "211"]}}, True)

# AI 模式未配置 -> 默认回退规则
score, passed, reasons = match({"name": "x"}, {"matching": {"mode": "ai", "ai": {}}})
ok = any("AI 失败已回退" in r for r in reasons)
print(f"{'PASS' if ok else 'FAIL'} | AI 未配置默认回退规则 | {'；'.join(reasons)}")
if not ok:
    failures += 1

# AI 模式未配置且禁用回退 -> 抛错
try:
    match({"name": "x"}, {"matching": {"mode": "ai", "ai": {"fallback_to_rules": False}}})
    print("FAIL | AI 未配置且禁用回退应抛错")
    failures += 1
except RuntimeError:
    print("PASS | AI 未配置且禁用回退正确抛错")

# 年限解析
assert extract_work_years("5年") == 5
assert extract_work_years("3-5年") == 3
assert extract_work_years("应届") == 0
print("PASS | 工作年限解析")

print("全部通过 ✓" if failures == 0 else f"有 {failures} 个用例失败 ✗")
sys.exit(0 if failures == 0 else 1)
