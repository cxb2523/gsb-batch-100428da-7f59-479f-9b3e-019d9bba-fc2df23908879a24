"""匹配引擎：
- rules 模式：筛选条件是硬性拦截，关键词等做软性打分；
- ai 模式：调用 OpenAI 兼容接口打分，失败可回退规则。
"""
import json
import re

try:
    import httpx
except ImportError:  # 离线环境兜底
    httpx = None


EDU_LEVEL = {"不限": 0, "中专": 0, "高中": 0, "大专": 1, "本科": 2, "硕士": 3, "博士": 4}


def to_number(text):
    m = re.search(r"-?\d+(\.\d+)?", str(text or ""))
    return float(m.group(0)) if m else None


def parse_education(text):
    t = str(text or "")
    for key in ["博士", "硕士", "本科", "大专", "中专", "高中"]:
        if key in t:
            return key
    return "不限"


def extract_work_years(text):
    t = str(text or "")
    if "应届" in t:
        return 0
    m = re.search(r"(\d+)\s*[-~到]\s*(\d+)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*年", t)
    return int(m.group(1)) if m else None


def parse_experience_ranges(experiences):
    """['1-3年','3-5年'] -> [(1,3),(3,5)]"""
    ranges = []
    for exp in experiences or []:
        m = re.search(r"(\d+)\s*[-~到]\s*(\d+)", str(exp))
        if m:
            ranges.append((int(m.group(1)), int(m.group(2))))
        elif "10年以上" in str(exp):
            ranges.append((10, 99))
    return ranges


def _gender_ok(want, actual):
    if not want or want == "all" or not actual:
        return True
    words = ["男", "male", "m"] if want == "male" else ["女", "female", "f"]
    a = str(actual).lower()
    return any(w in a for w in words)


def score_by_rules(profile, config):
    """返回 (score, passed, reasons)。硬性不满足直接拒绝。"""
    f = config.get("filters", {})
    rules = config.get("matching", {}).get("rules", {})
    weights = {
        "education": int(rules.get("education_weight", 20)),
        "work_years": int(rules.get("work_years_weight", 20)),
        "age": int(rules.get("age_weight", 10)),
        "gender": int(rules.get("gender_weight", 10)),
        "keyword": int(rules.get("keyword_weight", 40)),
    }
    total = sum(weights.values()) or 100
    reasons = []

    # ---------- 硬性筛选 ----------
    def fail(msg):
        reasons.append(msg)
        return False

    # 性别
    if not _gender_ok(f.get("gender"), profile.get("gender")):
        fail(f"性别不符（要求{f.get('gender')}，实际{profile.get('gender')}）")

    # 年龄
    age = profile.get("age")
    if age is not None and not (int(f.get("min_age", 0)) <= age <= int(f.get("max_age", 99))):
        fail(f"年龄不在区间（{age}）")

    # 学历多选
    edu = parse_education(profile.get("education"))
    edu_allowed = f.get("education") or []
    if edu_allowed and edu not in edu_allowed:
        fail(f"学历不符（{edu}，要求 {'/'.join(edu_allowed)}）")

    # 经验区间
    years = profile.get("work_years")
    exp_ranges = parse_experience_ranges(f.get("experience"))
    if years is not None and exp_ranges and not any(a <= years <= b for a, b in exp_ranges):
        fail(f"经验不符（{years}年，要求 {'/'.join(f.get('experience'))}）")

    if reasons:
        return 10, False, reasons

    # ---------- 软性打分 ----------
    keywords = [k for k in set((rules.get("keywords") or []) + (f.get("keywords") or [])) if k]
    profile_text = " ".join([
        str(profile.get("position") or ""),
        str(profile.get("company") or ""),
        str(profile.get("salary") or ""),
        " ".join(profile.get("tags") or []),
        str(profile.get("summary") or ""),
    ]).lower()

    if keywords:
        hit = sum(1 for k in keywords if str(k).lower() in profile_text)
        keyword_score = hit / len(keywords)
        if hit == 0:
            return 10, False, ["关键词无命中"]
    else:
        keyword_score = 1.0

    soft = keyword_score * weights["keyword"]
    if edu in edu_allowed or not edu_allowed:
        soft += weights["education"]
    if years is not None and (not exp_ranges or any(a <= years <= b for a, b in exp_ranges)):
        soft += weights["work_years"]
    if age is not None and int(f.get("min_age", 0)) <= age <= int(f.get("max_age", 99)):
        soft += weights["age"]
    if _gender_ok(f.get("gender"), profile.get("gender")):
        soft += weights["gender"]

    score = round(soft / total * 100)
    min_score = int(config.get("matching", {}).get("min_score", 60))
    passed = score >= min_score
    if not passed:
        reasons.append(f"总分 {score} < {min_score}")
    return score, passed, reasons


async def score_by_ai(profile, config):
    ai = config.get("matching", {}).get("ai", {})
    if not ai.get("api_url") or not ai.get("api_key"):
        raise RuntimeError("AI 接口未配置（api_url / api_key）")
    if httpx is None:
        raise RuntimeError("缺少 httpx 依赖")

    min_score = int(ai.get("min_score", config.get("matching", {}).get("min_score", 60)))
    payload = {
        "model": ai.get("model", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": ai.get("system_prompt", "")},
            {"role": "user", "content": json.dumps({
                "job_profile": config.get("filters"),
                "candidate": profile,
            }, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            ai["api_url"],
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {ai['api_key']}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _safe_json(content)
    score = max(0, min(100, int(parsed.get("score", 0) or 0)))
    return score, score >= min_score, parsed.get("reason", "")


def match(profile, config):
    """同步入口：rules 直接算；ai 用 asyncio 跑。"""
    mode = config.get("matching", {}).get("mode", "rules")
    if mode == "ai":
        import asyncio
        try:
            return asyncio.run(score_by_ai(profile, config))
        except Exception as e:
            fallback = config.get("matching", {}).get("ai", {}).get("fallback_to_rules", True)
            if fallback:
                score, passed, reasons = score_by_rules(profile, config)
                reasons.append(f"AI 失败已回退：{e}")
                return score, passed, reasons
            raise
    return score_by_rules(profile, config)


def _safe_json(text):
    s = str(text or "")
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {}
