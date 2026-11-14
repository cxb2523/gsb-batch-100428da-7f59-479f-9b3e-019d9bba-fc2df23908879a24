"""元学习：跨任务经验记忆。

- 执行轨迹持久化（JSONL）：任务目标、步骤、成败，供后续任务检索参考
- 词汇映射学习：用户描述 ↔ 实际控件名（如 "乘号" ↔ "Multiply by"），
  成功后自动记录，下次直接命中策略链第 0 级
- 结构化逐步轨迹（TraceRecorder）：每步落盘「元素表 + 决策 + 结果」，
  是 desktop-jev 小模型 SFT 的原料（research/desktop-jev/TRAINING.md §1）

存储位置：~/.screen_use/（可用 SCREEN_USE_HOME 环境变量覆盖）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path


def _default_home() -> Path:
    return Path(os.environ.get("SCREEN_USE_HOME", Path.home() / ".screen_use"))


def _tokenize(text: str) -> set[str]:
    """中英混合分词：英文按词，中文按字（简单够用）。"""
    text = text.lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    chars = set(re.findall(r"[一-鿿]", text))
    return words | chars


class ExperienceStore:
    def __init__(self, home: Path | None = None):
        self.home = home or _default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.traces_path = self.home / "traces.jsonl"
        self.vocab_path = self.home / "vocab.json"

    # ---- 执行轨迹 ----

    def record_trace(self, goal: str, result: dict) -> None:
        """任务结束后记录轨迹（只存摘要，不存截图）。"""
        entry = {
            "ts": time.time(),
            "goal": goal,
            "done": result["done"],
            "result": result["result"],
            "steps": [
                {"action": s["action"], "args": s["args"], "ok": s["ok"]}
                for s in result["steps"]
            ],
        }
        with open(self.traces_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def recall(self, goal: str, limit: int = 3) -> list[dict]:
        """按关键词重叠度检索相似历史任务（简单有效，零依赖）。"""
        if not self.traces_path.exists():
            return []
        query = _tokenize(goal)
        if not query:
            return []
        scored = []
        with open(self.traces_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                overlap = len(query & _tokenize(entry.get("goal", "")))
                if overlap > 0:
                    # 成功的经验优先，其次按重叠度
                    scored.append((entry.get("done", False), overlap, entry))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [e for _, _, e in scored[:limit]]

    # ---- 词汇映射 ----

    def _load_vocab(self) -> dict:
        if not self.vocab_path.exists():
            return {}
        try:
            return json.loads(self.vocab_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def learn_mapping(self, description: str, element_name: str, control_type: str = "") -> None:
        """记录 描述→控件名 的成功映射（重复成功会累计置信度）。"""
        key = description.strip().lower()
        if not key or not element_name:
            return
        vocab = self._load_vocab()
        entry = vocab.get(key, {"name": "", "control_type": "", "hits": 0})
        entry["name"] = element_name
        if control_type:
            entry["control_type"] = control_type
        entry["hits"] = entry.get("hits", 0) + 1
        vocab[key] = entry
        self.vocab_path.write_text(
            json.dumps(vocab, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def recall_mapping(self, description: str) -> dict | None:
        """查找已学到的映射。返回 {"name": ..., "control_type": ..., "hits": ...}。"""
        return self._load_vocab().get(description.strip().lower())

    # ---- 统计 ----

    def stats(self) -> dict:
        traces = 0
        successes = 0
        if self.traces_path.exists():
            with open(self.traces_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        traces += 1
                        successes += bool(entry.get("done"))
                    except json.JSONDecodeError:
                        continue
        return {
            "traces": traces,
            "successes": successes,
            "vocab_size": len(self._load_vocab()),
        }


# ---- 结构化逐步轨迹（desktop-jev 阶段 0：SFT 原料采集） ----

# 任务结束方式（end 行的 end_reason）：
#   done        正常完成（VLM/Jev 输出 done）
#   max_steps   达到最大步数仍未完成
#   jev_blocked Jev 认输升级（BLOCKED，cascade protocol §4）
#   interrupted 异常中断（观测/动作等抛出未捕获异常）——半截轨迹必须能和
#               正常结束区分开，否则训练侧会把残轨当完整样本
END_REASONS = ("done", "max_steps", "jev_blocked", "interrupted")


class TraceRecorder:
    """结构化逐步轨迹采集器：每次 run 产出一份可回放的任务记录。

    每个任务一个 JSONL 文件（traces_full/{task_id}.jsonl），按行顺序回放：
      1. meta 行：{"type": "meta", task_id, goal, start_ts, start_time}
      2. 每步一行：{"type": "step", goal, step_idx, foreground_title,
                   elements, action, args, ok, detail, source, ts}
      3. end 行：{"type": "end", end_reason, start_ts, end_ts, steps,
                  wall_ms, write_failures, error}

    2026-11 之前的旧格式没有 type 字段（每行都是步）；读取侧按
    `rec.get("type", "step") == "step"` 过滤即可同时兼容新旧文件。

    与 ExperienceStore.record_trace 的区别：record_trace 只存任务级摘要
    （action+args+ok），供检索；这里存的是**每步的完整观测**（UIA 元素表），
    是 desktop-jev 小模型的训练原料（research/desktop-jev/TRAINING.md §1）。
    不存截图（体积可控；需要时按 bbox 重截）。
    """

    def __init__(self, home: Path | str | None = None):
        self.dir = (Path(home) if home else _default_home()) / "traces_full"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path: Path | None = None
        self._goal: str = ""
        self._task_id: str = ""
        self._start_ts: float = 0.0

    @property
    def path(self) -> Path | None:
        """当前任务的轨迹文件路径（start_task 前为 None）。"""
        return self._path

    def start_task(self, goal: str) -> str:
        """开始一个任务的采集：写 meta 行（此处即建文件，失败调用方可整体降级）。

        返回 task_id（时间戳 + goal 短哈希，文件名安全）。
        """
        digest = hashlib.sha1(goal.encode("utf-8")).hexdigest()[:8]
        task_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{digest}"
        self._path = self.dir / f"{task_id}.jsonl"
        self._goal = goal
        self._task_id = task_id
        self._start_ts = time.time()
        self._write({
            "type": "meta",
            "task_id": task_id,
            "goal": goal,
            "start_ts": self._start_ts,
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(self._start_ts)),
        })
        return task_id

    def record_step(self, step_idx: int, foreground_title: str,
                    elements: list[dict], action: str, args: dict,
                    ok: bool, detail: str = "", source: str = "vlm_trace") -> None:
        """落盘一步观测+决策。须在 start_task 之后调用。

        source 标注这一步的决策来源（vlm_trace = 大模型驾驶 / jev = 小模型快路径），
        训练侧据此分层报告指标（TRAINING §1：合成准确率再高也不算数）。
        """
        entry = {
            "type": "step",
            "goal": self._goal,
            "step_idx": step_idx,
            "foreground_title": foreground_title,
            "elements": elements,
            "action": action,
            "args": args,
            "ok": ok,
            "detail": detail,
            "source": source,
            "ts": time.time(),
        }
        self._write(entry)

    def finish_task(self, end_reason: str, steps: int, wall_ms: float = 0.0,
                    write_failures: int = 0, error: str = "") -> None:
        """写 end 行收尾：结束方式 + 起止时间 + 步数 + 采集期写入失败统计。

        异常中断（end_reason="interrupted"）也必须调用——否则半截轨迹看起来
        像正常结束。error 传中断异常的简述（正常结束传 ""）。
        """
        if end_reason not in END_REASONS:
            raise ValueError(f"未知结束方式: {end_reason!r}（可选 {END_REASONS}）")
        end_ts = time.time()
        self._write({
            "type": "end",
            "task_id": self._task_id,
            "goal": self._goal,
            "end_reason": end_reason,
            "start_ts": self._start_ts,
            "end_ts": end_ts,
            "end_time": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(end_ts)),
            "steps": steps,
            "wall_ms": round(wall_ms, 1),
            "write_failures": write_failures,
            "error": error,
        })

    def _write(self, entry: dict) -> None:
        if self._path is None:
            raise RuntimeError("record_step 前请先 start_task")
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
