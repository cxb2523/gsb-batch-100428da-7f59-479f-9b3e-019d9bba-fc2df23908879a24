"""Ambiguity-aware UIA matching tests using a fake control tree.

These tests never touch the real desktop: the uiautomation import and the
window lookup are stubbed, while the production matching/recursion code
(`_bounded_search`, `_resolve_unique_control`, the MCP handlers) runs for real.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from win32_mcp_server.tools import uia
from win32_mcp_server.utils.errors import ToolError


class FakeRect:
    def __init__(self, left: int, top: int, width: int, height: int) -> None:
        self.left = left
        self.top = top
        self._width = width
        self._height = height

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height


class FakeValuePattern:
    def __init__(self, value: str) -> None:
        self.value = value
        self.sets: list[str] = []

    @property
    def Value(self) -> str:
        return self.value

    def SetValue(self, value: str) -> None:
        self.value = value
        self.sets.append(value)


class FakeInvokePattern:
    def __init__(self) -> None:
        self.invoked = 0

    def Invoke(self) -> None:
        self.invoked += 1


class FakeControl:
    """Minimal stand-in for a uiautomation control object."""

    def __init__(
        self,
        name: str = "",
        control_type: str = "ButtonControl",
        automation_id: str = "",
        rect: tuple[int, int, int, int] | None = None,
        value: str | None = None,
        children: list["FakeControl"] | None = None,
    ) -> None:
        self.Name = name
        self.ControlTypeName = control_type
        self.AutomationId = automation_id
        self.ClassName = ""
        self.BoundingRectangle = FakeRect(*rect) if rect else FakeRect(0, 0, 0, 0)
        self._children = list(children or [])
        self._value_pattern = FakeValuePattern(value) if value is not None else None
        self.invoke_pattern = FakeInvokePattern()
        self.clicked = 0
        self.focused = 0
        self.sent_keys: list[str] = []

    def GetChildren(self) -> list["FakeControl"]:
        return list(self._children)

    def GetValuePattern(self) -> FakeValuePattern | None:
        return self._value_pattern

    def GetTextPattern(self) -> None:
        return None

    def GetTogglePattern(self) -> None:
        return None

    def GetInvokePattern(self) -> FakeInvokePattern:
        return self.invoke_pattern

    @property
    def IsEnabled(self) -> bool:
        return True

    def Click(self) -> None:
        self.clicked += 1

    def SetFocus(self) -> None:
        self.focused += 1

    def SendKeys(self, keys: str, waitTime: float = 0.0) -> None:
        self.sent_keys.append(keys)


class FakeUiautomation:
    """Enough of the uiautomation module surface for the UIA tools."""

    class UIAutomationInitializerInThread:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *exc: object) -> None:
            return None

    def __init__(self, root: FakeControl) -> None:
        self._root = root
        # _get_control_class looks these up by uiautomation class name.
        # Skip WindowControl: that name is the window-lookup method below.
        for class_name in uia._CONTROL_TYPE_MAP.values():
            if class_name == "WindowControl":
                continue
            setattr(self, class_name, type(class_name, (), {}))

    def WindowControl(self, **_kwargs: Any) -> FakeControl:
        return self._root

    def GetFocusedControl(self) -> FakeControl:
        return self._root


def _install_fake_auto(
    monkeypatch: pytest.MonkeyPatch,
    root: FakeControl,
) -> FakeUiautomation:
    fake_auto = FakeUiautomation(root)
    monkeypatch.setattr(uia, "_import_uia", lambda: fake_auto)
    monkeypatch.setattr(
        uia,
        "_find_window_control",
        lambda auto, title: auto.WindowControl(),
    )
    return fake_auto


def _build_tree() -> FakeControl:
    """Window with two same-named buttons, a unique button, and an edit field."""
    ok_a = FakeControl(
        name="OK",
        control_type="ButtonControl",
        automation_id="okPrimary",
        rect=(10, 10, 60, 30),
    )
    ok_b = FakeControl(
        name="OK",
        control_type="ButtonControl",
        automation_id="okSecondary",
        rect=(100, 10, 60, 30),
    )
    save = FakeControl(
        name="Save",
        control_type="ButtonControl",
        automation_id="saveButton",
        rect=(200, 10, 60, 30),
    )
    name_field = FakeControl(
        name="Name",
        control_type="EditControl",
        automation_id="nameInput",
        rect=(10, 80, 200, 24),
        value="initial",
    )
    return FakeControl(
        name="Demo Window",
        control_type="WindowControl",
        children=[ok_a, ok_b, save, name_field],
    )


# ---------------------------------------------------------------------------
# Shared matching semantics
# ---------------------------------------------------------------------------


def test_find_and_resolver_share_matching_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    # Find uses partial name matching; both flows must see the same controls.
    content = asyncio.run(uia.handle_uia_find_control({"window_title": "Demo Window", "name": "ok"}))
    payload = json.loads(content[0].text)
    assert payload["found"] == 2
    assert payload["match_status"] == "multiple"
    assert payload["truncated"] is False
    assert {c["automation_id"] for c in payload["controls"]} == {"okPrimary", "okSecondary"}

    # Exact-name resolution over the same tree hits the same two controls.
    with pytest.raises(ToolError, match="Ambiguous control match: 2 controls") as exc_info:
        uia._resolve_unique_control(root, name="OK", aid="", ctrl_type="", exact_name=True)
    candidates = exc_info.value.details["candidates"]
    assert {c["automation_id"] for c in candidates} == {"okPrimary", "okSecondary"}


# ---------------------------------------------------------------------------
# uia_find_control: unique / multiple / truncated / none
# ---------------------------------------------------------------------------


def test_find_control_reports_unique_match(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    content = asyncio.run(uia.handle_uia_find_control({"window_title": "Demo Window", "name": "Save"}))
    payload = json.loads(content[0].text)

    assert payload["found"] == 1
    assert payload["match_status"] == "unique"
    assert payload["truncated"] is False
    assert "hint" not in payload
    assert payload["controls"][0]["automation_id"] == "saveButton"


def test_find_control_flags_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    root = FakeControl(
        name="Many",
        control_type="WindowControl",
        children=[
            FakeControl(
                name="Item",
                control_type="ButtonControl",
                automation_id=f"btn{i}",
                rect=(i, 0, 10, 10),
            )
            for i in range(5)
        ],
    )
    _install_fake_auto(monkeypatch, root)

    content = asyncio.run(
        uia.handle_uia_find_control({"window_title": "Many", "control_type": "button", "max_results": 3})
    )
    payload = json.loads(content[0].text)

    assert payload["found"] == 3
    assert payload["truncated"] is True
    assert payload["max_results"] == 3
    assert payload["match_status"] == "multiple"
    assert "truncated" in payload["hint"]


def test_find_control_not_truncated_when_results_equal_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeControl(
        name="Exact",
        control_type="WindowControl",
        children=[
            FakeControl(
                name="Item",
                control_type="ButtonControl",
                automation_id=f"btn{i}",
                rect=(i, 0, 10, 10),
            )
            for i in range(3)
        ],
    )
    _install_fake_auto(monkeypatch, root)

    content = asyncio.run(
        uia.handle_uia_find_control({"window_title": "Exact", "control_type": "button", "max_results": 3})
    )
    payload = json.loads(content[0].text)

    assert payload["found"] == 3
    assert payload["truncated"] is False
    assert payload["match_status"] == "multiple"


def test_find_control_no_match_gives_readable_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    content = asyncio.run(uia.handle_uia_find_control({"window_title": "Demo Window", "name": "Nonexistent"}))
    payload = json.loads(content[0].text)

    assert payload["found"] == 0
    assert payload["controls"] == []
    assert payload["match_status"] == "none"
    assert payload["truncated"] is False
    assert "uia_inspect_window" in payload["hint"]


# ---------------------------------------------------------------------------
# By-name tools: unique hit keeps legacy behavior
# ---------------------------------------------------------------------------


def test_click_unique_control_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    result = asyncio.run(uia.handle_uia_click_control({"window_title": "Demo Window", "name": "Save"}))

    assert result["clicked"] is True
    assert result["method"] == "uia_invoke"
    assert result["control"]["automation_id"] == "saveButton"
    save_button = next(c for c in root.GetChildren() if c.AutomationId == "saveButton")
    assert save_button.invoke_pattern.invoked == 1
    assert save_button.clicked == 0


def test_get_value_unique_control_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    result = asyncio.run(
        uia.handle_uia_get_control_value({"window_title": "Demo Window", "name": "Name", "control_type": "edit"})
    )

    assert result["value"] == "initial"
    assert result["automation_id"] == "nameInput"
    assert result["control_type"] == "EditControl"


def test_set_value_unique_control_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    result = asyncio.run(
        uia.handle_uia_set_control_value(
            {
                "window_title": "Demo Window",
                "name": "Name",
                "control_type": "edit",
                "value": "updated",
            }
        )
    )

    assert result["set"] is True
    name_field = next(c for c in root.GetChildren() if c.AutomationId == "nameInput")
    assert name_field.GetValuePattern().Value == "updated"
    assert result["control"]["automation_id"] == "nameInput"


# ---------------------------------------------------------------------------
# By-name tools: duplicate exact names must error with candidate details
# ---------------------------------------------------------------------------


def test_click_ambiguous_control_raises_with_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    with pytest.raises(ToolError, match="Ambiguous control match") as exc_info:
        asyncio.run(uia.handle_uia_click_control({"window_title": "Demo Window", "name": "OK"}))

    err = exc_info.value
    assert err.suggestion is not None
    assert "automation_id" in err.suggestion
    details = err.details
    assert details["results_truncated"] is False
    candidates = details["candidates"]
    assert len(candidates) == 2
    assert {c["control_type"] for c in candidates} == {"ButtonControl"}
    assert {c["automation_id"] for c in candidates} == {"okPrimary", "okSecondary"}
    for candidate in candidates:
        assert set(candidate["bounds"]) == {"x", "y", "width", "height"}
    # No control was actually clicked.
    for child in root.GetChildren():
        assert child.clicked == 0
        assert child.invoke_pattern.invoked == 0


def test_get_value_ambiguous_control_raises_with_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    with pytest.raises(ToolError, match="Ambiguous control match") as exc_info:
        asyncio.run(
            uia.handle_uia_get_control_value({"window_title": "Demo Window", "name": "OK", "control_type": "button"})
        )

    candidates = exc_info.value.details["candidates"]
    assert len(candidates) == 2
    assert all(c["control_type"] == "ButtonControl" for c in candidates)


def test_set_value_ambiguous_control_raises_and_does_not_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)
    duplicated_fields = FakeControl(
        name="Dup",
        control_type="WindowControl",
        children=[
            FakeControl(
                name="Dup",
                control_type="EditControl",
                automation_id=f"dup{i}",
                rect=(0, i * 30, 100, 24),
                value="original",
            )
            for i in range(2)
        ],
    )
    _install_fake_auto(monkeypatch, duplicated_fields)

    with pytest.raises(ToolError, match="Ambiguous control match") as exc_info:
        asyncio.run(uia.handle_uia_set_control_value({"window_title": "Dup", "name": "Dup", "value": "clobber"}))

    candidates = exc_info.value.details["candidates"]
    assert {c["automation_id"] for c in candidates} == {"dup0", "dup1"}
    for child in duplicated_fields.GetChildren():
        assert child.GetValuePattern().Value == "original"
        assert child.sent_keys == []


def test_automation_id_disambiguates_same_named_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    result = asyncio.run(
        uia.handle_uia_click_control({"window_title": "Demo Window", "name": "OK", "automation_id": "okSecondary"})
    )

    assert result["clicked"] is True
    secondary = next(c for c in root.GetChildren() if c.AutomationId == "okSecondary")
    primary = next(c for c in root.GetChildren() if c.AutomationId == "okPrimary")
    assert secondary.invoke_pattern.invoked == 1
    assert primary.invoke_pattern.invoked == 0


# ---------------------------------------------------------------------------
# By-name tools: no match stays a readable error
# ---------------------------------------------------------------------------


def test_click_missing_control_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _build_tree()
    _install_fake_auto(monkeypatch, root)

    with pytest.raises(ToolError, match="Control not found") as exc_info:
        asyncio.run(uia.handle_uia_click_control({"window_title": "Demo Window", "name": "Ghost"}))

    assert "uia_find_control" in (exc_info.value.suggestion or "")


def test_dispatch_renders_ambiguity_details_as_json() -> None:
    from win32_mcp_server.registry import registry

    root = _build_tree()

    class _PatchAuto:
        @staticmethod
        def patched_import() -> Any:
            return FakeUiautomation(root)

    original_import = uia._import_uia
    original_find_window = uia._find_window_control
    uia._import_uia = lambda: FakeUiautomation(root)  # type: ignore[method-assign]
    uia._find_window_control = lambda auto, title: root  # type: ignore[method-assign]
    try:
        content = asyncio.run(
            registry.dispatch(
                "uia_click_control",
                {"window_title": "Demo Window", "name": "OK"},
            )
        )
    finally:
        uia._import_uia = original_import  # type: ignore[method-assign]
        uia._find_window_control = original_find_window  # type: ignore[method-assign]

    payload = json.loads(content[0].text)
    assert payload["error"] is True
    assert "Ambiguous" in payload["message"]
    assert payload["suggestion"]
    assert len(payload["details"]["candidates"]) == 2
    assert payload["details"]["results_truncated"] is False


# ---------------------------------------------------------------------------
# Search bounds still use the existing depth / max_results constants
# ---------------------------------------------------------------------------


def test_search_respects_depth_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    deep_leaf = FakeControl(
        name="Deep",
        control_type="ButtonControl",
        automation_id="deep",
        rect=(0, 0, 10, 10),
    )
    node = deep_leaf
    for depth in range(uia._MAX_UIA_SEARCH_DEPTH + 2):
        node = FakeControl(
            name=f"Level{depth}",
            control_type="PaneControl",
            automation_id=f"pane{depth}",
            children=[node],
        )
    root = FakeControl(name="Deep Window", control_type="WindowControl", children=[node])
    _install_fake_auto(monkeypatch, root)

    matches, truncated = uia._bounded_search(
        root,
        name="deep",
        aid="",
        ctrl_type="",
        exact_name=False,
        max_results=uia._MAX_UIA_RESULTS,
    )

    assert matches == []
    assert truncated is False


def test_resolver_truncation_reported_for_large_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeControl(
        name="Huge",
        control_type="WindowControl",
        children=[
            FakeControl(
                name="Same",
                control_type="ButtonControl",
                automation_id=f"btn{i}",
                rect=(i, 0, 10, 10),
            )
            for i in range(uia._MAX_UIA_RESULTS + 5)
        ],
    )
    _install_fake_auto(monkeypatch, root)

    with pytest.raises(ToolError, match="Ambiguous control match") as exc_info:
        uia._resolve_unique_control(root, name="Same", aid="", ctrl_type="", exact_name=True)

    details = exc_info.value.details
    assert details["results_truncated"] is True
    assert len(details["candidates"]) == uia._MAX_UIA_RESULTS
    assert "more than" in str(exc_info.value)


def test_module_does_not_reintroduce_old_helpers() -> None:
    assert not hasattr(uia, "_search_tree")
    assert not hasattr(uia, "_search_tree_objects")
    assert not hasattr(uia, "_find_control_in_window")
    assert not hasattr(uia, "_find_control_object_in_window")
    assert isinstance(uia._MAX_UIA_RESULTS, int)
    assert uia._MAX_UIA_SEARCH_DEPTH == 8
