# -*- coding: utf-8 -*-
"""Loading of the legacy ``args.py`` configuration file.

The original project keeps its user facing configuration in
``olx_scraper/args.py`` (one directory above this package).  That file is still
the single place where ``city``, ``product`` and ``minimumprice`` are
configured.  This module only locates it, so the spider keeps working both when
Scrapy is started from ``olx_scraper/`` and when the package is imported from
the repository root (or from a test runner).
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Callable

Info = Callable[[str], str]

# Used only when args.py cannot be found at all.  The values match the
# behaviour of args.py for unknown keys.
_DEFAULTS = {"city": "", "product": "", "minimumprice": ""}


def _default_info(key: str) -> str:
    return _DEFAULTS.get(str(key).lower(), "")


def _load_module_from_path(path: Path) -> ModuleType | None:
    """Load an arbitrary ``args.py`` without polluting ``sys.modules``."""
    try:
        spec = importlib.util.spec_from_file_location("_olx_user_args", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # pragma: no cover - defensive, a broken file is not fatal
        return None


def _extract_info(module: ModuleType | None) -> Info | None:
    if module is None:
        return None
    info = getattr(module, "info", None)
    if callable(info):
        return info
    return None


def load_info() -> Info:
    """Return the ``info`` callable from the user configuration file.

    Resolution order:

    1. ``olx_scraper.args`` if it happens to be importable (that is what the
       original source expected).
    2. ``<project>/args.py`` next to this package.
    3. ``args.py`` in the current working directory.
    4. A safe default that returns ``""``.
    """

    # 1) Normal import.  Depending on sys.path this can resolve either to the
    #    package submodule or to the repository level namespace package.
    try:
        module = importlib.import_module("olx_scraper.args")
    except Exception:
        module = None
    info = _extract_info(module)
    if info is not None:
        return info

    # 2) / 3) Locate the file explicitly.
    project_dir = Path(__file__).resolve().parent.parent
    candidates = [project_dir / "args.py", Path.cwd() / "args.py"]
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - exotic file systems
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        info = _extract_info(_load_module_from_path(resolved))
        if info is not None:
            return info

    return _default_info


__all__ = ["Info", "load_info"]
