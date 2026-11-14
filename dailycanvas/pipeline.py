"""流水线编排：采集 -> 合成 -> 渲染，含降级与失败退出策略。"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional

from .fetch import fetch_all, load_config
from .render import write_outputs
from .synthesize import synthesize


class PublishError(RuntimeError):
    """无法出刊（全部源失败或无有效条目）。"""


def run_pipeline(
    config_path,
    output_dir,
    edition: Optional[str] = None,
) -> dict:
    """执行一期出刊。

    - 单源失败：仅降级该源，其余源照常合成。
    - 全部源失败或没有任何有效条目：抛出 PublishError，不写任何输出文件。
    - edition 缺省为当天日期（YYYY-MM-DD）；显式传入可复现同一期。
    """
    config = load_config(config_path)
    edition = edition or _dt.date.today().isoformat()

    outcomes = fetch_all(config)
    if not outcomes:
        raise PublishError("源清单中没有启用的源，拒绝发布空刊")
    if all(o.status == "failed" for o in outcomes):
        detail = "; ".join(f"{o.name}: {o.error}" for o in outcomes)
        raise PublishError(f"全部 {len(outcomes)} 个源均失败，拒绝发布空刊。{detail}")

    items = synthesize(outcomes, config["max_items"])
    if not items:
        raise PublishError("采集成功但没有有效条目，拒绝发布空刊")

    paths = write_outputs(edition, items, outcomes, Path(output_dir))
    return {
        "edition": edition,
        "item_count": len(items),
        "outcomes": outcomes,
        "paths": paths,
    }
