"""Loading proxies from a file.

Rules:
  - blank lines are ignored
  - lines starting with '#' are comments
  - entries are validated; invalid ones are reported, not fatal
  - duplicates are dropped (first occurrence wins)
  - order is preserved
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Proxy


@dataclass(slots=True)
class LoadResult:
    proxies: list[Proxy]
    duplicates: int = 0
    invalid: list[tuple[int, str, str]] | None = None  # (line number, raw text, reason)

    def __post_init__(self) -> None:
        if self.invalid is None:
            self.invalid = []


def load_proxies(path: Path) -> LoadResult:
    """Read and validate a proxy list file.

    Raises FileNotFoundError when the file does not exist.
    An empty result is returned to the caller (which decides how to treat it).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"proxy list not found: {path}") from None
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: not valid UTF-8: {exc}") from exc

    proxies: list[Proxy] = []
    seen: set[tuple[str, str, int, str | None, str | None]] = set()
    invalid: list[tuple[int, str, str]] = []
    duplicates = 0

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        try:
            proxy = Proxy.parse(line)
        except ValueError as exc:
            invalid.append((lineno, line, str(exc)))
            continue

        key = proxy.dedupe_key()
        if key in seen:
            duplicates += 1
            continue

        seen.add(key)
        proxies.append(proxy)

    return LoadResult(proxies=proxies, duplicates=duplicates, invalid=invalid)
