"""Configuration: defaults, optional TOML file, CLI overrides.

Priority: CLI arguments > config.toml > defaults.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "proxyrotate"
DEFAULT_CONFIG_FILE = CONFIG_DIR / "config.toml"
DEFAULT_PROXY_FILE = CONFIG_DIR / "proxies.txt"

VALID_LOG_LEVELS = ("debug", "info", "warning", "error")


class ConfigError(ValueError):
    """Raised for an invalid configuration value."""


DEFAULT_PROCESS_DNS = "1.1.1.1"
PROCESS_NAME_MODES = ("exact", "substring")


@dataclass(slots=True)
class AppConfig:
    proxy_file: Path = DEFAULT_PROXY_FILE
    sing_box: str | None = None
    rotation_interval: float = 60.0
    rotation_requests: int | None = None
    rotation_mb: float | None = None
    failure_cooldown: float = 30.0
    cooldown_max: float = 600.0
    health_check: bool = True
    health_timeout: float = 5.0
    log_level: str = "info"
    process_name: str | None = None
    process_name_mode: str = "exact"
    process_dns: str = DEFAULT_PROCESS_DNS
    config_file: Path | None = field(default=None, repr=False)


def load_config(path: Path | None = None) -> AppConfig:
    """Load configuration from a TOML file.

    A missing file is not an error; defaults are used. Unknown keys are
    reported via ConfigError so typos do not silently change behaviour.
    """
    path = path or DEFAULT_CONFIG_FILE
    config = AppConfig(config_file=path)

    if not path.exists():
        return config

    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a table at the top level")

    known = {f.name for f in fields(AppConfig)} - {"config_file"}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{path}: unknown option(s): {', '.join(sorted(unknown))}")

    for key in known & set(data):
        value = data[key]
        if key == "proxy_file":
            config.proxy_file = _as_path(path, key, value)
        elif key == "sing_box":
            config.sing_box = _as_str(path, key, value)
        elif key == "rotation_interval":
            config.rotation_interval = _as_number(path, key, value, minimum=0)
        elif key == "rotation_requests":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{path}: {key} must be a whole number")
            if value < 1:
                raise ConfigError(f"{path}: {key} must be >= 1")
            config.rotation_requests = value
        elif key == "rotation_mb":
            number = _as_number(path, key, value, minimum=0)
            if number <= 0:
                raise ConfigError(f"{path}: {key} must be > 0")
            config.rotation_mb = number
        elif key == "failure_cooldown":
            config.failure_cooldown = _as_number(path, key, value, minimum=0)
        elif key == "cooldown_max":
            config.cooldown_max = _as_number(path, key, value, minimum=0.001)
        elif key == "health_check":
            config.health_check = _as_bool(path, key, value)
        elif key == "health_timeout":
            config.health_timeout = _as_number(path, key, value, minimum=0.001)
        elif key == "log_level":
            level = _as_str(path, key, value).lower()
            if level not in VALID_LOG_LEVELS:
                raise ConfigError(
                    f"{path}: {key} must be one of: {', '.join(VALID_LOG_LEVELS)}"
                )
            config.log_level = level
        elif key == "process_name":
            name = _as_str(path, key, value)
            if not name.strip():
                raise ConfigError(f"{path}: {key} must not be empty")
            config.process_name = name
        elif key == "process_name_mode":
            mode = _as_str(path, key, value).lower()
            if mode not in PROCESS_NAME_MODES:
                raise ConfigError(
                    f"{path}: {key} must be one of: {', '.join(PROCESS_NAME_MODES)}"
                )
            config.process_name_mode = mode
        elif key == "process_dns":
            config.process_dns = _as_str(path, key, value)

    return config


def apply_cli_overrides(config: AppConfig, *, proxy_file: Path | None,
                        sing_box: str | None, every: float | None,
                        cooldown: float | None, health_check: bool | None,
                        health_timeout: float | None,
                        log_level: str | None,
                        rotation_requests: int | None = None,
                        rotation_mb: float | None = None,
                        process_name: str | None = None,
                        process_name_mode: str | None = None,
                        process_dns: str | None = None) -> AppConfig:
    """Apply explicitly-provided CLI values on top of the loaded config."""
    if proxy_file is not None:
        config.proxy_file = proxy_file
    if sing_box is not None:
        config.sing_box = sing_box
    if every is not None:
        config.rotation_interval = every
    if cooldown is not None:
        config.failure_cooldown = cooldown
    if health_check is not None:
        config.health_check = health_check
    if health_timeout is not None:
        config.health_timeout = health_timeout
    if log_level is not None:
        config.log_level = log_level.lower()
    if rotation_requests is not None:
        config.rotation_requests = rotation_requests
    if rotation_mb is not None:
        config.rotation_mb = rotation_mb
    if process_name is not None:
        config.process_name = process_name
    if process_name_mode is not None:
        config.process_name_mode = process_name_mode
    if process_dns is not None:
        config.process_dns = process_dns
    return config


def expand_path(value: str) -> Path:
    return Path(value).expanduser()


def _as_path(file: Path, key: str, value: object) -> Path:
    path = expand_path(_as_str(file, key, value))
    if not str(path):
        raise ConfigError(f"{file}: {key} must not be empty")
    return path


def _as_str(file: Path, key: str, value: object) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{file}: {key} must be a string")
    return value


def _as_number(file: Path, key: str, value: object, *, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{file}: {key} must be a number")
    number = float(value)
    if number < minimum:
        raise ConfigError(f"{file}: {key} must be >= {minimum}")
    return number


def _as_bool(file: Path, key: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{file}: {key} must be true or false")
    return value
