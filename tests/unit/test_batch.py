"""Unit tests for the batch_action tool.

Covers partial failure, total failure, empty serial list, duplicate serials,
per-action dispatch, and rejection of unknown actions. Devices are mocked;
per-serial behavior is controlled by overriding ``u2.connect`` to return a
dedicated mock per serial.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError
from pytest_mock import MockerFixture

from u2mcp.tools.batch import batch_action


def make_serial_devices(mocker: MockerFixture, mock_u2_module, devices: dict) -> None:
    """Route u2.connect(serial) to a dedicated mock device per serial."""
    mock_u2_module.connect.side_effect = lambda serial: devices[serial]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_click_success(mock_u2_device) -> None:
    """A click batch runs on every device and reports success."""
    results = await batch_action(["emulator-5554"], "click", {"x": 100, "y": 200})

    assert results == [{"serial": "emulator-5554", "ok": True, "error": ""}]
    mock_u2_device.click.assert_called_once_with(100, 200)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_press_key(mock_u2_device) -> None:
    """press_key dispatches to device.press."""
    results = await batch_action(["emulator-5554"], "press_key", {"key": "home"})

    assert results == [{"serial": "emulator-5554", "ok": True, "error": ""}]
    mock_u2_device.press.assert_called_once_with("home")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_app_start(mock_u2_device) -> None:
    """app_start dispatches to device.app_start with defaults for optional params."""
    results = await batch_action(["emulator-5554"], "app_start", {"package_name": "com.example.app"})

    assert results == [{"serial": "emulator-5554", "ok": True, "error": ""}]
    mock_u2_device.app_start.assert_called_once_with("com.example.app", None, False, False)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_xpath_click(mock_u2_device) -> None:
    """xpath_click dispatches to device.xpath(xpath).click_exists(timeout)."""
    results = await batch_action(["emulator-5554"], "xpath_click", {"xpath": "//*[@text='OK']", "timeout": 5.0})

    assert results == [{"serial": "emulator-5554", "ok": True, "error": ""}]
    mock_u2_device.xpath.assert_called_once_with("//*[@text='OK']")
    mock_u2_device.xpath.return_value.click_exists.assert_called_once_with(5.0)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_partial_failure(mocker: MockerFixture, mock_u2_module) -> None:
    """One failing device does not interrupt the others and is reported."""
    good_device = mocker.MagicMock()
    bad_device = mocker.MagicMock()
    bad_device.click.side_effect = RuntimeError("device offline")
    make_serial_devices(mocker, mock_u2_module, {"good-1": good_device, "bad-1": bad_device})

    results = await batch_action(["good-1", "bad-1"], "click", {"x": 1, "y": 2})

    assert results == [
        {"serial": "good-1", "ok": True, "error": ""},
        {"serial": "bad-1", "ok": False, "error": "device offline"},
    ]
    good_device.click.assert_called_once_with(1, 2)
    bad_device.click.assert_called_once_with(1, 2)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_all_failed_returns_full_results(mocker: MockerFixture, mock_u2_module) -> None:
    """When every device fails, the full per-device result list is returned, not raised."""
    devices = {}
    for serial in ("bad-1", "bad-2"):
        device = mocker.MagicMock()
        device.press.side_effect = RuntimeError(f"{serial} unreachable")
        devices[serial] = device
    make_serial_devices(mocker, mock_u2_module, devices)

    results = await batch_action(["bad-1", "bad-2"], "press_key", {"key": "back"})

    assert results == [
        {"serial": "bad-1", "ok": False, "error": "bad-1 unreachable"},
        {"serial": "bad-2", "ok": False, "error": "bad-2 unreachable"},
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_empty_serial_list(mock_u2_module) -> None:
    """An empty serial list returns an empty result without connecting."""
    results = await batch_action([], "click", {"x": 1, "y": 2})

    assert results == []
    mock_u2_module.connect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_duplicate_serials(mocker: MockerFixture, mock_u2_module) -> None:
    """A repeated serial is executed once per occurrence, each with its own result."""
    device = mocker.MagicMock()
    make_serial_devices(mocker, mock_u2_module, {"dup-1": device})

    results = await batch_action(["dup-1", "dup-1"], "click", {"x": 3, "y": 4})

    assert results == [
        {"serial": "dup-1", "ok": True, "error": ""},
        {"serial": "dup-1", "ok": True, "error": ""},
    ]
    assert device.click.call_count == 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_unknown_action_rejected(mock_u2_module) -> None:
    """An unknown action is rejected with the supported list, before any device is touched."""
    with pytest.raises(ToolError, match=r"Unknown action 'reboot'.*app_start.*click.*press_key.*xpath_click"):
        await batch_action(["emulator-5554"], "reboot", {})

    mock_u2_module.connect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_action_missing_param_rejected(mock_u2_module) -> None:
    """A missing required parameter is rejected before any device is touched."""
    with pytest.raises(ToolError, match="requires parameter 'y'"):
        await batch_action(["emulator-5554"], "click", {"x": 1})

    mock_u2_module.connect.assert_not_called()
