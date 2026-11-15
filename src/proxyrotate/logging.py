"""Logging setup for ProxyRotate.

All ProxyRotate log records go to a file under the user cache directory.
The console is never written to by the logging framework: lifecycle
messages are printed explicitly (to stderr) before the target application
starts, and nothing is printed while the application owns the terminal.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOG_DIR = Path(
    os.environ.get("PROXYROTATE_CACHE_DIR", Path.home() / ".cache" / "proxyrotate")
)
LOG_FILE = LOG_DIR / "proxyrotate.log"

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_configured = False


def level_from_name(name: str) -> int:
    try:
        return _LEVELS[name.lower()]
    except KeyError:
        raise ValueError(
            f"invalid log level {name!r} (expected one of: {', '.join(_LEVELS)})"
        ) from None


def setup_logging(level_name: str) -> logging.Logger:
    """Configure the proxyrotate logger with a file handler.

    Safe to call more than once; existing handlers are replaced only when
    needed so repeated setup does not duplicate records.
    """
    global _configured
    logger = logging.getLogger("proxyrotate")

    if not _configured:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
        _configured = True

    logger.setLevel(level_from_name(level_name))
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"proxyrotate.{name}")
