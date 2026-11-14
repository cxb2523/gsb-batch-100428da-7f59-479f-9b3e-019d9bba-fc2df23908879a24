"""采集阶段：读取源清单，并发抓取，逐源记录结局。"""

from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, List

from .model import Entry, EntryError, SourceOutcome

DEFAULT_TIMEOUT = 10.0  # 秒
DEFAULT_RETRIES = 2  # 首次失败后的重试次数
DEFAULT_MAX_ITEMS = 50


class ConfigError(ValueError):
    """源清单配置非法。"""


def load_config(config_path: str | Path) -> dict:
    """读取源清单与流水线参数。

    配置格式::

        {
          "max_items": 50,
          "timeout_seconds": 10,
          "retries": 2,
          "sources": [
            {"name": "...", "url": "...", "weight": 1.0, "enabled": true}
          ]
        }

    url 支持 http(s)://、file:// 以及相对配置文件所在目录的本地路径。
    """
    path = Path(config_path)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取源清单 {path}: {exc}") from exc
    if not isinstance(cfg, dict) or not isinstance(cfg.get("sources"), list):
        raise ConfigError("源清单缺少 sources 数组")

    base = path.parent
    sources = []
    for i, raw in enumerate(cfg["sources"]):
        if not isinstance(raw, dict):
            raise ConfigError(f"sources[{i}] 不是对象")
        name = raw.get("name")
        url = raw.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"sources[{i}] 缺少 name")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(f"源 {name!r} 缺少 url")
        weight = raw.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            raise ConfigError(f"源 {name!r} 的 weight 非法")
        sources.append(
            {
                "name": name.strip(),
                "url": _resolve_url(url.strip(), base),
                "weight": float(weight),
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    return {
        "sources": sources,
        "max_items": int(cfg.get("max_items", DEFAULT_MAX_ITEMS)),
        "timeout": float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT)),
        "retries": int(cfg.get("retries", DEFAULT_RETRIES)),
    }


def _resolve_url(url: str, base: Path) -> str:
    if url.startswith(("http://", "https://", "file://")):
        return url
    # 本地相对路径：相对配置文件目录解析，统一转成 file:// URL
    return (base / url).resolve().as_uri()


def _read_url(url: str, timeout: float) -> bytes:
    if url.startswith("file://"):
        # 本地文件源：用于样例数据与测试，不触网
        from urllib.parse import unquote, urlparse

        parsed = urlparse(url)
        local = Path(unquote(parsed.path.lstrip("/"))) if _is_windows_path(parsed) else Path(unquote(parsed.path))
        return local.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "dailycanvas/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 源地址来自受信配置
        return resp.read()


def _is_windows_path(parsed) -> bool:
    # file:///D:/... 形式
    p = parsed.path
    return len(p) >= 3 and p[0] == "/" and p[2] == ":" and p[1].isalpha()


def _parse_payload(data: bytes) -> List[Any]:
    doc = json.loads(data.decode("utf-8"))
    if isinstance(doc, dict):
        items = doc.get("items")
    else:
        items = doc
    if not isinstance(items, list):
        raise ValueError("载荷中没有条目数组（items）")
    return items


def fetch_source(source: dict, timeout: float, retries: int) -> SourceOutcome:
    """抓取单个源，带超时与有限重试；结局（含失败原因）写入返回值。"""
    outcome = SourceOutcome(name=source["name"], url=source["url"], weight=source["weight"])
    start = time.monotonic()
    last_error: str | None = None
    raw_items: List[Any] | None = None

    for attempt in range(1, retries + 2):  # 1 次首发 + retries 次重试
        outcome.attempts = attempt
        try:
            raw_items = _parse_payload(_read_url(source["url"], timeout))
            last_error = None
            break
        except Exception as exc:  # 网络、超时、JSON 解析等统一记录
            last_error = f"{type(exc).__name__}: {exc}"
    outcome.elapsed_ms = (time.monotonic() - start) * 1000.0

    if last_error is not None:
        outcome.status = "failed"
        outcome.error = last_error
        return outcome

    for raw in raw_items:
        try:
            outcome.entries.append(Entry.from_raw(raw, source["name"], source["weight"]))
        except EntryError:
            outcome.dropped += 1  # 脏数据：跳过但计数，不拖垮整个源
    return outcome


def fetch_all(config: dict) -> List[SourceOutcome]:
    """并发抓取所有启用的源；返回按源名排序的结局列表（保证确定性）。"""
    enabled = [s for s in config["sources"] if s["enabled"]]
    if not enabled:
        return []
    workers = min(8, len(enabled))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(
            pool.map(lambda s: fetch_source(s, config["timeout"], config["retries"]), enabled)
        )
    # 并发完成顺序不定，统一按源名排序，保证同日重复运行结果一致
    outcomes.sort(key=lambda o: o.name)
    return outcomes
