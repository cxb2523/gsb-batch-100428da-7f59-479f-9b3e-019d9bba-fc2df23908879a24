"""Per-chart history snapshots, used to derive previous ranks where a site gives none."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


def period_key(period: str | None, fallback: str) -> str:
    """Turn '2026/10/26 ~ 2026/11/01' into '2026-10-26_2026-11-01' (sortable file name)."""
    dates = re.findall(r"\d{4}/\d{1,2}(?:/\d{1,2})?", period or "")
    if not dates:
        return fallback
    parts = []
    for d in dates:
        nums = [int(x) for x in d.split("/")]
        parts.append("-".join(f"{n:02d}" if i else str(n) for i, n in enumerate(nums)))
    return "_".join(parts)


def normalize(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"\s+", "", text)


def song_key(row: dict) -> str:
    """Stable identity for a song within one source."""
    if row.get("song_no"):
        return "no:" + row["song_no"]
    if row.get("song_url"):
        return "url:" + row["song_url"]
    return "ta:" + normalize(row.get("title")) + "|" + normalize(row.get("artist"))


class History:
    def __init__(self, root: Path):
        self.root = root

    def _dir(self, source: str, chart: str) -> Path:
        return self.root / source / chart

    def save(self, source: str, chart: dict) -> None:
        d = self._dir(source, chart["id"])
        d.mkdir(parents=True, exist_ok=True)
        rows = [
            {k: r.get(k) for k in ("rank", "title", "artist", "song_no", "song_url") if r.get(k) is not None}
            for r in chart["rows"]
        ]
        payload = {"period": chart.get("period"), "rows": rows}
        (d / f"{chart['period_key']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
        )

    def _snapshots(self, source: str, chart_id: str) -> list[Path]:
        d = self._dir(source, chart_id)
        if not d.is_dir():
            return []
        return sorted(d.glob("*.json"))

    def _read(self, path: Path) -> dict | None:
        """Read one snapshot; a corrupt file counts as 'no snapshot', never as a crash."""
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def latest_snapshot(self, source: str, chart_id: str) -> tuple[str | None, dict | None]:
        """(period_key, payload) of this chart's newest snapshot.

        A missing directory gives (None, None); a corrupt newest file does too, so a
        broken history never crashes a source run. Each chart id has its own folder,
        so callers can never pick up another chart's snapshot (daily vs. weekly keys).
        """
        snaps = self._snapshots(source, chart_id)
        if not snaps:
            return None, None
        newest = snaps[-1]
        data = self._read(newest)
        if data is None:
            return None, None
        return newest.stem, data

    def latest(self, source: str, chart_id: str) -> dict | None:
        """Payload of the newest snapshot saved for this chart."""
        return self.latest_snapshot(source, chart_id)[1]

    def previous(self, source: str, chart_id: str, key: str) -> dict | None:
        """Latest snapshot strictly older than `key`."""
        older = [p for p in self._snapshots(source, chart_id) if p.stem < key]
        for path in reversed(older):  # newest first; skip unreadable files
            data = self._read(path)
            if data is not None:
                return data
        return None

    def fill_previous_ranks(self, source: str, chart: dict) -> None:
        """Set last_rank / is_new from the previous snapshot. Leaves None when there is none."""
        prev = self.previous(source, chart["id"], chart["period_key"])
        if prev is None:
            for r in chart["rows"]:
                r["last_rank"] = None
                r["is_new"] = None
            return
        ranks = {song_key(r): r["rank"] for r in prev["rows"]}
        for r in chart["rows"]:
            last = ranks.get(song_key(r))
            r["last_rank"] = last
            r["is_new"] = last is None
