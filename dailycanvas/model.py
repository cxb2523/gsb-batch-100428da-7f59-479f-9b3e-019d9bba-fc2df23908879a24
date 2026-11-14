"""数据模型与条目校验。"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Optional


class EntryError(ValueError):
    """条目缺少必需字段或字段非法。"""


def parse_published(value: Any) -> _dt.datetime:
    """把 published 字段解析为 UTC 感知 datetime。

    接受 "YYYY-MM-DD" 或 ISO 8601 日期时间（可带 Z 后缀）。
    """
    if not isinstance(value, str) or not value.strip():
        raise EntryError("published 缺失或不是字符串")
    text = value.strip()
    try:
        if "T" not in text and " " not in text:
            d = _dt.date.fromisoformat(text)
            return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc)
        dt = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EntryError(f"published 无法解析: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


@dataclass(frozen=True)
class Entry:
    """一条规范化后的条目。"""

    id: str
    title: str
    url: str
    published: _dt.datetime
    source: str
    weight: float

    @classmethod
    def from_raw(cls, raw: Any, source: str, weight: float) -> "Entry":
        if not isinstance(raw, dict):
            raise EntryError("条目不是对象")
        eid = raw.get("id")
        if not isinstance(eid, str) or not eid.strip():
            raise EntryError("缺少 id")
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            raise EntryError(f"条目 {eid!r} 缺少 title")
        published = parse_published(raw.get("published"))
        url = raw.get("url")
        if url is not None and not isinstance(url, str):
            raise EntryError(f"条目 {eid!r} 的 url 非法")
        return cls(
            id=eid.strip(),
            title=title.strip(),
            url=url or "",
            published=published,
            source=source,
            weight=float(weight),
        )

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "published": self.published.isoformat().replace("+00:00", "Z"),
            "source": self.source,
            "weight": self.weight,
        }


@dataclass
class SourceOutcome:
    """单个源的采集结局，用于追溯。"""

    name: str
    url: str
    weight: float
    status: str = "ok"  # "ok" | "failed"
    attempts: int = 0
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    entries: list = field(default_factory=list)  # list[Entry]
    dropped: int = 0  # 脏数据（缺字段等）被丢弃的条数

    @property
    def item_count(self) -> int:
        return len(self.entries)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "weight": self.weight,
            "status": self.status,
            "attempts": self.attempts,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error": self.error,
            "item_count": self.item_count,
            "dropped": self.dropped,
        }
