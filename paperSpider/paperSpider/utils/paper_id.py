# -*- coding: utf-8 -*-
"""论文文档标识生成与字段安全转换工具。

设计目标：
1. 不再把 ``paper_source``（详情页来源 URL，可能为空或重复）作为 ES 文档主键，
   而是根据论文自身的稳定属性（DOI、标题、作者、年份等）派生确定性标识；
2. 同一篇论文无论被处理多少次，生成的标识都相同（重复抓取 => 更新同一文档）；
3. 不同论文不会生成同一标识（通过分类型指纹 + 完整字段参与哈希保证）；
4. 整型字段（引用量、年份）无法解析时按缺失处理，而不是写入非法值。
"""
import hashlib
import re

# 标识前缀，避免与历史数据中直接以 paper_source 作为 _id 的文档发生命名空间冲突
_ID_PREFIX = "paper_"

# 用于兜底指纹的稳定字段顺序（顺序固定，保证哈希可复现）。
# 不含引用量等易变字段；摘要/关键词等内容字段参与指纹，保证仅来源重复的
# 不同论文也能被区分开。
_FINGERPRINT_FIELDS = (
    "paper_title",
    "paper_writer",
    "paper_time",
    "paper_source",
    "paper_DOI",
    "paper_abstract",
    "paper_keywords",
    "paper_download_link",
)

# DOI 常见前缀（大小写不敏感），命中则剥离
_DOI_PREFIX_RE = re.compile(r"^\s*(?:doi:|https?://(?:dx\.)?doi\.org/)\s*", re.IGNORECASE)
# 合法 DOI 的主体：10.<注册机构号>/<后缀>，后缀允许常见可见字符
_DOI_BODY_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


def normalize_text(value):
    """规范化文本：None/非字符串安全转字符串，去除首尾空白并压缩内部连续空白。

    结果保留原大小写之外的原始内容（中文不受影响），用于稳定地参与标识生成。
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_doi(value):
    """提取并规范化 DOI，无法识别时返回空字符串。"""
    text = normalize_text(value)
    if not text:
        return ""
    text = _DOI_PREFIX_RE.sub("", text)
    match = _DOI_BODY_RE.search(text)
    if not match:
        return ""
    # DOI 本身大小写不敏感，统一转小写，保证同一 DOI 只产生一个标识
    return match.group(0).rstrip(".,;)").lower()


def safe_int(value):
    """把任意输入安全转换为 int；无法解析时返回 ``None``（按缺失处理）。

    支持的输入：
    - int/float（float 需为有限数且无小数部分歧义时按截断处理）；
    - 字符串，自动提取其中第一段数字，兼容 "12"、"被引 12 次"、"1,234" 等；
    - None、空串、"暂无" 等无法解析的输入 => None。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool 是 int 的子类，但语义上不是合法的引用量/年份
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    # 去掉千分位分隔符后提取第一段整数（含负号）
    match = re.search(r"-?\d+", text.replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group(0))
    except (TypeError, ValueError):
        return None


def _sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _normalize_writers(value):
    """作者字段可能是列表或字符串，统一为去空白后的有序列表。"""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        writers = [normalize_text(v) for v in value]
    else:
        writers = [normalize_text(v) for v in re.split(r"[,;，；]", str(value))]
    return [w for w in writers if w]


def build_identity(item):
    """根据 item 构造论文的稳定身份指纹。

    返回 ``(kind, key, fingerprint)``：
    - ``kind`` 身份依据类型：``doi`` / ``title`` / ``source`` / ``unknown``；
    - ``key`` 规范化后的身份主键（DOI 原文 / 规范化标题 / 来源）；
    - ``fingerprint`` 由全部稳定字段构成的规范化字符串，用于：
      1) 同标识冲突时判断“是不是同一篇论文”；
      2) 兜底场景派生唯一标识。

    优先级：DOI > 标题（+作者+年份辅助）> 来源 URL > 无法识别。
    """
    def get(field):
        # 同时兼容 scrapy.Item 与普通 dict
        if field not in item:
            return ""
        return item.get(field, "")

    doi = normalize_doi(get("paper_DOI"))
    title = normalize_text(get("paper_title"))
    source = normalize_text(get("paper_source"))
    year = safe_int(get("paper_time"))
    writers = _normalize_writers(get("paper_writer"))

    fingerprint_parts = []
    for field in _FINGERPRINT_FIELDS:
        value = get(field)
        if field == "paper_writer":
            value = writers
        elif field == "paper_DOI":
            value = doi
        elif field in ("paper_time",):
            value = "" if year is None else str(year)
        else:
            value = normalize_text(value)
        fingerprint_parts.append(
            "%s=%s" % (field, "|".join(value) if isinstance(value, (list, tuple)) else value)
        )
    fingerprint = "\n".join(fingerprint_parts)

    if doi:
        return "doi", doi, fingerprint
    if title:
        # 标题做大小写归一，作者与年份作为辅助区分（同名不同论文）
        key_parts = [title.lower()]
        if writers:
            key_parts.append("writers=%s" % "|".join(writers).lower())
        if year is not None:
            key_parts.append("year=%d" % year)
        # 走到 title 分支说明 DOI 为空，保留占位以固定字段结构
        key_parts.append("doi=")
        return "title", "\x1f".join(key_parts), fingerprint
    if source:
        # 来源 URL 可能重复，generate_paper_id 会让全字段指纹共同参与哈希，
        # 保证不同论文即使共用同一来源 URL 也能派生不同标识
        return "source", source, fingerprint
    return "unknown", "", fingerprint


def generate_paper_id(item):
    """为论文生成稳定且唯一的 ES 文档 ``_id``；无法识别时返回 ``None``。

    规则（确定性，无随机因子）：

    1. 有 DOI：``paper_doi_<sha1(规范化 DOI)>``；
    2. 无 DOI 但有标题：``paper_title_<sha1(规范化标题 + 作者 + 年份)>``；
    3. 只有来源：``paper_src_<sha1(来源 URL + 全字段指纹)>``，
       全字段指纹参与哈希，避免不同论文共用同一来源 URL 时撞标识；
    4. 标题、DOI、来源全部缺失：返回 ``None``，由调用方决定跳过。

    同一论文（身份字段不变）多次处理得到相同标识；不同论文因身份依据
    不同或指纹不同，不会得到相同标识。前缀同时隔离历史 ``paper_source`` 主键。
    """
    kind, key, fingerprint = build_identity(item)
    if kind == "doi":
        return _ID_PREFIX + "doi_" + _sha1(key)
    if kind == "title":
        return _ID_PREFIX + "title_" + _sha1(key)
    if kind == "source":
        return _ID_PREFIX + "src_" + _sha1(key + "\n" + fingerprint)
    return None


def disambiguate_id(base_id, item):
    """当 ``base_id`` 已被“另一篇论文”占用时，派生带完整指纹的兜底标识。

    结果仍然是确定性的：同一篇论文在同一 ``base_id`` 冲突下反复处理，
    每次得到相同的兜底标识，因此重复抓取依旧更新同一文档而不是新增。
    """
    _, _, fingerprint = build_identity(item)
    return "%sfb_%s" % (base_id, _sha1(fingerprint))
