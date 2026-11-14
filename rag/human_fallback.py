"""Human fallback queue and conversation context management.

PendingQueue — per-customer newest-only dedup, 24h TTL, expiry detection.
ContextStore — per-customer conversation history persistence (JSONL).
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TTL_SECONDS = 24 * 3600  # 24 hours


@dataclass
class PendingItem:
    """One pending human-confirmation item."""
    timestamp: float          # when it entered the queue
    customer_name: str
    customer_message: str
    ai_reply: str
    confidence: float         # retrieval cosine score
    guard_decision: str       # "low_risk" or from guard
    status: str = "pending"   # "pending" | "expired"

    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > TTL_SECONDS


class PendingQueue:
    """Per-customer newest-only pending message queue with TTL.

    - Each customer only keeps the newest pending item.
    - Older items from the same customer are auto-cleaned.
    - Items > 24h are expired on load/check.
    - Expiry detection on send: if customer sent new messages since
      the queued item, mark as expired.
    """

    def __init__(self, persist_path: str = "data/state/pending_queue.json"):
        self._lock = threading.Lock()
        self._items: dict[str, PendingItem] = {}  # keyed by customer_name
        self._cleaned_counts: dict[str, int] = {}   # per-customer cleanup count
        self._path = Path(persist_path)
        self._load()

    def push(self, customer_name: str, customer_message: str,
             ai_reply: str, confidence: float = 0.0,
             guard_decision: str = "low_risk") -> Optional[PendingItem]:
        """Add or replace pending item for a customer.

        Returns the displaced (cleaned) item if one existed, else None.
        """
        new_item = PendingItem(
            timestamp=time.time(),
            customer_name=customer_name,
            customer_message=customer_message,
            ai_reply=ai_reply,
            confidence=confidence,
            guard_decision=guard_decision,
        )
        with self._lock:
            old = self._items.pop(customer_name, None)
            if old is not None:
                self._cleaned_counts[customer_name] = \
                    self._cleaned_counts.get(customer_name, 0) + 1
            self._items[customer_name] = new_item
        self._save()
        return old

    def get_all(self) -> list[PendingItem]:
        """Return active (non-expired) pending items, newest first."""
        with self._lock:
            active = []
            expired_names = []
            for name, item in self._items.items():
                if item.is_expired():
                    expired_names.append(name)
                else:
                    active.append(item)
            for name in expired_names:
                del self._items[name]
        if expired_names:
            self._save()
        active.sort(key=lambda i: i.timestamp, reverse=True)
        return active

    def get(self, customer_name: str) -> Optional[PendingItem]:
        """Return a pending item without removing it."""
        with self._lock:
            return self._items.get(customer_name)

    def remove(self, customer_name: str) -> Optional[PendingItem]:
        """Remove and return a pending item (e.g., after send/ignore)."""
        with self._lock:
            item = self._items.pop(customer_name, None)
        if item:
            self._save()
        return item

    def mark_expired(self, customer_name: str) -> Optional[PendingItem]:
        """Mark item as expired (customer sent newer messages)."""
        with self._lock:
            item = self._items.get(customer_name)
            if item:
                item.status = "expired"
                self._save()
        return item

    @property
    def count(self) -> int:
        with self._lock:
            return len([i for i in self._items.values() if not i.is_expired()])

    def get_cleanup_count(self, customer_name: str) -> int:
        with self._lock:
            return self._cleaned_counts.get(customer_name, 0)

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "items": {
                    name: {
                        "timestamp": item.timestamp,
                        "customer_name": item.customer_name,
                        "customer_message": item.customer_message,
                        "ai_reply": item.ai_reply,
                        "confidence": item.confidence,
                        "guard_decision": item.guard_decision,
                        "status": item.status,
                    }
                    for name, item in self._items.items()
                },
                "cleaned_counts": self._cleaned_counts,
            }
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"Failed to save pending queue: {e}")

    def _load(self):
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            loaded = 0
            for name, raw in data.get("items", {}).items():
                ts = raw.get("timestamp", 0)
                if now - ts > TTL_SECONDS:
                    continue  # skip expired
                self._items[name] = PendingItem(
                    timestamp=ts,
                    customer_name=raw.get("customer_name", name),
                    customer_message=raw.get("customer_message", ""),
                    ai_reply=raw.get("ai_reply", ""),
                    confidence=raw.get("confidence", 0.0),
                    guard_decision=raw.get("guard_decision", ""),
                    status=raw.get("status", "pending"),
                )
                loaded += 1
            self._cleaned_counts = data.get("cleaned_counts", {})
            if loaded:
                logger.info(f"Loaded {loaded} pending items from {self._path}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load pending queue: {e}")


class ContextStore:
    """Per-customer conversation history stored as JSONL files.

    Each customer gets ``data/context/{name}.jsonl`` with one JSON
    line per conversation turn: ``{ts, role, text}`` where role is
    "customer" or "assistant".
    """

    def __init__(self, base_dir: str = "data/context"):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def append(self, customer_name: str, role: str, text: str):
        """Record one conversation turn."""
        entry = {
            "ts": time.time(),
            "role": role,
            "text": text[:1000],
        }
        safe_name = _safe_filename(customer_name)
        with open(self._dir / f"{safe_name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_recent(self, customer_name: str, turns: int = 3) -> list[dict]:
        """Return the most recent N conversation turns."""
        safe_name = _safe_filename(customer_name)
        path = self._dir / f"{safe_name}.jsonl"
        if not path.exists():
            return []
        lines = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        lines.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read context for {customer_name}: {e}")
            return []
        return lines[-turns:]


def _safe_filename(name: str) -> str:
    """Replace path-unsafe characters in customer names."""
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()
