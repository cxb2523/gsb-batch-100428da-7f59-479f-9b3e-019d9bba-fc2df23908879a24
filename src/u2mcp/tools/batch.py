"""Batch execution of a single action across multiple devices.

The ``batch_action`` tool runs one action on each device in a serial list,
one device at a time, and reports a per-device result. A device that fails
(offline, disconnected, operation error) never aborts the remaining devices,
and even when every device fails the full per-device result list is returned
instead of raising, so the caller can tell exactly which devices to retry.

Device access goes through :func:`u2mcp.tools.device.use_device`, reusing its
connection caching and per-device locking like every other tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from anyio import to_thread
from fastmcp.exceptions import ToolError

from ..mcp import mcp
from .device import use_device

__all__ = ("batch_action",)

ActionHandler = Callable[[Any, dict[str, Any]], Awaitable[None]]


async def _run_click(device: Any, params: dict[str, Any]) -> None:
    await to_thread.run_sync(device.click, params["x"], params["y"])


async def _run_press_key(device: Any, params: dict[str, Any]) -> None:
    await to_thread.run_sync(device.press, params["key"])


async def _run_app_start(device: Any, params: dict[str, Any]) -> None:
    await to_thread.run_sync(
        device.app_start,
        params["package_name"],
        params.get("activity"),
        bool(params.get("wait", False)),
        bool(params.get("stop", False)),
    )


async def _run_xpath_click(device: Any, params: dict[str, Any]) -> None:
    xpath = params["xpath"]
    timeout = params["timeout"]
    await to_thread.run_sync(lambda: device.xpath(xpath).click_exists(timeout))


_ACTIONS: dict[str, ActionHandler] = {
    "click": _run_click,
    "press_key": _run_press_key,
    "app_start": _run_app_start,
    "xpath_click": _run_xpath_click,
}

# Required parameters per action, with the accepted types for each.
_REQUIRED_PARAMS: dict[str, dict[str, tuple[type, ...]]] = {
    "click": {"x": (int,), "y": (int,)},
    "press_key": {"key": (str,)},
    "app_start": {"package_name": (str,)},
    "xpath_click": {"xpath": (str,), "timeout": ((int, float),)},
}


def _validate_params(action: str, params: dict[str, Any]) -> None:
    """Validate shared action parameters once, before touching any device."""
    for name, types in _REQUIRED_PARAMS[action].items():
        if name not in params:
            raise ToolError(f"Action {action!r} requires parameter {name!r}")
        value = params[name]
        if not isinstance(value, types) or isinstance(value, bool):
            expected = " or ".join(t.__name__ for t in types)
            raise ToolError(f"Action {action!r} parameter {name!r} must be of type {expected}, got {type(value).__name__}")


@mcp.tool("batch_action", tags={"batch", "batch:action"})
async def batch_action(serials: list[str], action: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run one action on multiple devices and report per-device results.

    Devices are processed sequentially in the given order. A failure on one
    device (unreachable, disconnected, operation error) does not interrupt
    the remaining devices, and the full per-device result list is returned
    even when every device fails. Duplicate serials are executed once per
    occurrence, in order.

    Supported actions and their parameters:
        - click: x (int), y (int)
        - press_key: key (str), e.g. home, back, enter, volume_up
        - app_start: package_name (str), optional activity (str), wait (bool), stop (bool)
        - xpath_click: xpath (str), timeout (number)

    Args:
        serials (list[str]): Android device serial numbers, in execution order.
            An empty list returns an empty result.
        action (str): Action name, one of: click, press_key, app_start, xpath_click.
        params (dict[str, Any] | None): Parameters for the action, shared by all devices.

    Returns:
        list[dict[str, Any]]: One entry per serial, in input order:
            - serial (str): Device serial number
            - ok (bool): True when the action succeeded on this device
            - error (str): Failure reason, empty string on success
    """
    handler = _ACTIONS.get(action)
    if handler is None:
        supported = ", ".join(sorted(_ACTIONS))
        raise ToolError(f"Unknown action {action!r}. Supported actions: {supported}")

    params = dict(params or {})
    _validate_params(action, params)

    results: list[dict[str, Any]] = []
    for serial in serials:
        try:
            async with use_device(serial) as device:
                await handler(device, params)
        except Exception as e:  # noqa: BLE001 - per-device isolation: any failure is reported, never raised
            results.append({"serial": serial, "ok": False, "error": str(e)})
        else:
            results.append({"serial": serial, "ok": True, "error": ""})
    return results
