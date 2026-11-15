"""
索引值规范化与可核对索引清单构建

索引源节点按列提取索引时，原始单元格可能存在：
- 空单元格 / 仅含空白的单元格
- 数字与文本混排（如 1001 与 "1001"）
- 首尾空白（如 " 1001 "）

本模块负责把这些原始值规范化为唯一索引值，并生成一份可核对的索引清单。
清单对每个唯一索引值只保留一条记录，记录内逐项列出构成它的原始值形态、
出现次数、是否因空值被跳过、以及是否被合并 / 规范化并归并到哪个索引值。
"""

from collections import OrderedDict
from datetime import date, datetime
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..models import (
    IndexManifestEntry,
    IndexManifestOriginalValue,
    IndexManifestSummary,
    IndexValue,
)

# 原始值类型
RAW_TYPE_TEXT = "text"
RAW_TYPE_NUMBER = "number"
RAW_TYPE_BOOLEAN = "boolean"
RAW_TYPE_DATETIME = "datetime"
RAW_TYPE_EMPTY = "empty"

# 空值条目在清单中的聚合键（所有空值形态汇总为一条跳过记录）
_SKIPPED_KEY = "__skipped__"


def is_missing_value(value: Any) -> bool:
    """判断一个值是否为空（None / NaN / NaT 等）"""
    if value is None:
        return True

    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False

    # pd.isna 对数组会返回数组，这里只接受标量结果
    if isinstance(result, (bool, np.bool_)):
        return bool(result)
    return False


def classify_raw_value(value: Any) -> Tuple[Any, str]:
    """
    将原始单元格值转换为便于展示与比较的形式。

    Returns:
        (展示值, 原始类型)
        展示值：None / str / int / float / bool
        原始类型：text / number / boolean / datetime / empty
    """
    if is_missing_value(value):
        return None, RAW_TYPE_EMPTY

    # bool 是 int 的子类，需要先判断
    if isinstance(value, (bool, np.bool_)):
        return bool(value), RAW_TYPE_BOOLEAN

    if isinstance(value, (int, np.integer)):
        return int(value), RAW_TYPE_NUMBER

    if isinstance(value, (float, np.floating)):
        numeric = float(value)
        if np.isnan(numeric) or np.isinf(numeric):
            return None, RAW_TYPE_EMPTY
        # 保留浮点原样（1001.0 仍展示为 1001.0），与下游 astype(str) 的比较保持一致
        return numeric, RAW_TYPE_NUMBER

    if isinstance(value, str):
        # 保留原始首尾空白，便于清单核对
        return value, RAW_TYPE_TEXT

    if isinstance(value, (pd.Timestamp, datetime, date)):
        return str(value), RAW_TYPE_DATETIME

    return str(value), RAW_TYPE_TEXT


def _display_to_text(display: Any, raw_type: str) -> str:
    """把展示值转换成用于与索引值比较的字符串"""
    if raw_type == RAW_TYPE_EMPTY:
        return ""
    if raw_type == RAW_TYPE_BOOLEAN:
        return "True" if display else "False"
    return str(display)


def normalize_index_value(value: Any) -> Optional[str]:
    """
    将一个值规范化为索引值。

    规则：
    - 空值 / 仅含空白的文本 -> None（视为空值，会被跳过）
    - 文本去掉首尾空白（不改变大小写、不解析数字，保留如 "002" 的前导零）
    - 整数或整数值浮点数统一为不带小数点的十进制文本（1001 与 1001.0 -> "1001"）
    - 布尔值统一为 "True" / "False"
    """
    display, raw_type = classify_raw_value(value)

    if raw_type == RAW_TYPE_EMPTY:
        return None

    if raw_type == RAW_TYPE_TEXT:
        stripped = display.strip()
        return stripped or None

    return _display_to_text(display, raw_type)


def resolve_index_value(raw_value: Any, cleaned_value: Any) -> Optional[str]:
    """
    根据原始值与清洗后的值确定该行的索引值。

    优先使用清洗后的值（保证与下游节点实际处理的数据一致）；
    当清洗把非空原始值丢弃时（例如数值列被强制转换），退回使用原始值，
    避免把原本有值的行误判成空值。
    """
    normalized = normalize_index_value(cleaned_value)
    if normalized is not None:
        return normalized
    return normalize_index_value(raw_value)


def build_index_manifest(
    raw_values: Sequence[Any],
    cleaned_values: Optional[Sequence[Any]] = None,
) -> Tuple[List[IndexValue], List[IndexManifestEntry], IndexManifestSummary]:
    """
    根据原始列值和清洗后的列值构建索引值与索引清单。

    Args:
        raw_values: 原始 Excel 单元格值（保留首尾空白等原始形态）
        cleaned_values: 经过数据清洗后的值；用于判断空值与最终的索引值。
            省略时直接使用 raw_values。

    Returns:
        (index_values, index_manifest, manifest_summary)

        - index_values: 去重后的规范化索引值（保持首次出现顺序）
        - index_manifest: 每个唯一索引值一条记录，空值汇总为一条跳过记录；
            同一清单内 index_value 不重复
        - manifest_summary: 汇总信息

    Raises:
        ValueError: 原始值与清洗后值长度不一致
    """
    if cleaned_values is None:
        cleaned_values = raw_values

    if len(raw_values) != len(cleaned_values):
        raise ValueError(
            "Raw column values and cleaned column values have different lengths: "
            f"{len(raw_values)} != {len(cleaned_values)}"
        )

    # 索引值 -> 累加器（按首次出现顺序）；空值汇总到同一个累加器
    accumulators: "OrderedDict[str, dict]" = OrderedDict()
    skipped_accumulator: Optional[dict] = None

    def new_accumulator(index_value: Optional[str]) -> dict:
        return {
            "index_value": index_value,
            "count": 0,
            # (raw_type, display) -> 原始形态明细
            "originals": OrderedDict(),
        }

    for raw_value, cleaned_value in zip(raw_values, cleaned_values):
        index_value = resolve_index_value(raw_value, cleaned_value)
        display, raw_type = classify_raw_value(raw_value)
        form_key = (raw_type, display)

        if index_value is None:
            if skipped_accumulator is None:
                skipped_accumulator = new_accumulator(None)
            accumulator = skipped_accumulator
            # 空值所有形态共用一条跳过记录，排序时放在首次出现的位置
            if _SKIPPED_KEY not in accumulators:
                accumulators[_SKIPPED_KEY] = skipped_accumulator
        else:
            accumulator = accumulators.get(index_value)
            if accumulator is None:
                accumulator = new_accumulator(index_value)
                accumulators[index_value] = accumulator

        accumulator["count"] += 1
        original = accumulator["originals"].get(form_key)
        if original is None:
            original = {
                "original_value": display,
                "raw_type": raw_type,
                "count": 0,
            }
            accumulator["originals"][form_key] = original
        original["count"] += 1

    manifest: List[IndexManifestEntry] = []
    index_values: List[IndexValue] = []
    merged_index_count = 0
    original_value_count = 0

    for accumulator in accumulators.values():
        index_value = accumulator["index_value"]
        skipped = index_value is None
        originals = list(accumulator["originals"].values())

        # 选出“代表形态”：优先选与索引值文本一致的形态（出现次数多者优先），
        # 否则选出现次数最多的形态。它用于条目顶层的 original_value。
        representative_position: Optional[int] = None
        if not skipped and originals:
            same_text = [
                position
                for position, original in enumerate(originals)
                if _display_to_text(original["original_value"], original["raw_type"])
                == index_value
            ]
            candidates = same_text or list(range(len(originals)))
            representative_position = max(
                candidates,
                key=lambda position: (
                    originals[position]["count"],
                    originals[position]["raw_type"] == RAW_TYPE_TEXT,
                    -position,
                ),
            )

        original_entries: List[IndexManifestOriginalValue] = []
        for position, original in enumerate(originals):
            original_text = _display_to_text(
                original["original_value"], original["raw_type"]
            )
            # 以下情况都视为被规范化 / 合并，并标注归并到的索引值：
            # 1. 原始值与索引值文本不同（如 " 1001 "、"00123"）
            # 2. 同一索引下存在多个原始形态，且该形态不是代表形态
            merged = bool(
                not skipped
                and (
                    original_text != index_value
                    or (len(originals) > 1 and position != representative_position)
                )
            )
            original_entries.append(
                IndexManifestOriginalValue(
                    original_value=original["original_value"],
                    raw_type=original["raw_type"],
                    count=original["count"],
                    skipped=skipped,
                    merged=merged,
                    merged_into=index_value if merged else None,
                )
            )

        entry_merged = any(original.merged for original in original_entries)
        representative = (
            original_entries[representative_position]
            if representative_position is not None
            and representative_position < len(original_entries)
            else None
        )

        manifest.append(
            IndexManifestEntry(
                index_value=index_value,
                original_value=(
                    representative.original_value if representative else None
                ),
                count=accumulator["count"],
                skipped=skipped,
                merged=entry_merged,
                original_values=original_entries,
            )
        )

        original_value_count += len(original_entries)
        if entry_merged:
            merged_index_count += 1
        if not skipped and index_value is not None:
            index_values.append(IndexValue(index_value))

    summary = IndexManifestSummary(
        total_rows=len(raw_values),
        unique_index_count=len(index_values),
        skipped_count=(
            skipped_accumulator["count"] if skipped_accumulator is not None else 0
        ),
        merged_index_count=merged_index_count,
        manifest_entry_count=len(manifest),
        original_value_count=original_value_count,
    )

    return index_values, manifest, summary
