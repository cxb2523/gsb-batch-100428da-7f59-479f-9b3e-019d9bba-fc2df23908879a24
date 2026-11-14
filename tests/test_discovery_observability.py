"""Session discovery observability, over a local fake COM.

No pywin32, no SAP GUI: FakeWin32Com resolves ROT names the way
win32com.client.GetObject does, FakePythoncom enumerates the ROT the way
pythoncom does, and the engines/connections/sessions are plain objects
with the COM shape (Children.Count, Children(i), Info, ...).

Covered: a stale cached engine is dropped, re-resolved and the drop is
recorded; the unsuffixed SAPGUISERVER entry is used only when no suffixed
entry exists; a ROT enumeration failure falls back to SAP Logon and every
connection carries the marker; out-of-range indexes and a scripting-
disabled connection are refused with a readable error and bind nothing.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mcp_sap_gui.sap_controller import SAPGUIController, SAPGUIError

# ---------------------------------------------------------------------------
# Fake COM objects
# ---------------------------------------------------------------------------

class FakeChildren:
    """A COM collection: Count plus call-by-index."""

    def __init__(self, items=()):
        self._items = list(items)

    @property
    def Count(self):  # noqa: N802 - COM name
        return len(self._items)

    def __call__(self, index):
        return self._items[index]


class FakeEngine:
    """A scripting engine. fail_probe simulates a dead process proxy: every
    access raises, as a cached engine does after its process restarted."""

    def __init__(self, connections=(), fail_probe=False):
        self._children = FakeChildren(connections)
        self.fail_probe = fail_probe
        self.MajorVersion = 8100
        self.Patchlevel = 0

    @property
    def Children(self):  # noqa: N802 - COM name
        if self.fail_probe:
            raise RuntimeError("The RPC server is unavailable.")
        return self._children


class FakeConnection:
    def __init__(self, cid, sessions=(), description="", disabled=False):
        self.Id = cid
        self.Description = description
        self.DisabledByServer = disabled
        self.Children = FakeChildren(sessions)


class FakeSession:
    def __init__(self, sid, system="DEV", client="100", user="USER1",
                 tcode="SESSION_MANAGER", number=1,
                 title="DEV(1)/100 SAP Easy Access"):
        self.Id = sid
        self.Busy = False
        self.Info = SimpleNamespace(
            SystemName=system, SystemNumber="00", Client=client, User=user,
            Transaction=tcode, SessionNumber=number, Language="EN",
            Program="SAPMSYST", ScreenNumber=100, ScriptingModeReadOnly=False,
        )
        self._title = title

    def findById(self, _element_id):  # noqa: N802 - COM name
        return SimpleNamespace(Text=self._title)


class FakeWin32Com:
    """Stands in for win32com.client: GetObject resolves a ROT name.

    engines maps a ROT name to an engine, or to an Exception raised instead
    (an entry whose process is gone). lookups records every resolution.
    """

    def __init__(self, engines):
        self.engines = engines
        self.lookups = []

    def GetObject(self, rot_name):  # noqa: N802 - COM name
        self.lookups.append(rot_name)
        target = self.engines.get(rot_name)
        if target is None:
            raise RuntimeError(f"Operation unavailable: {rot_name}")
        if isinstance(target, Exception):
            raise target
        return SimpleNamespace(GetScriptingEngine=target)


class FakeMoniker:
    def __init__(self, name):
        self._name = name

    def GetDisplayName(self, _bind_ctx, _leftover):  # noqa: N802 - COM name
        return self._name


class FakePythoncom:
    """Stands in for pythoncom: the Running Object Table enumeration."""

    def __init__(self, rot_names=(), enum_error=None):
        self._rot_names = list(rot_names)
        self._enum_error = enum_error

    def CoInitialize(self):  # noqa: N802 - COM name
        pass

    def CreateBindCtx(self, _reserved):  # noqa: N802 - COM name
        return None

    def GetRunningObjectTable(self):  # noqa: N802 - COM name
        if self._enum_error is not None:
            raise self._enum_error
        names = self._rot_names

        class _Rot:
            def EnumRunning(self):  # noqa: N802 - COM name
                return iter([FakeMoniker(name) for name in names])

        return _Rot()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_controller(engines, rot_names=(), enum_error=None):
    """A controller over the fake COM; returns (controller, com, pythoncom)."""
    controller = SAPGUIController()
    com = FakeWin32Com(engines)
    controller._win32com = com
    pythoncom = FakePythoncom(rot_names, enum_error)
    return controller, com, pythoncom


def run(pythoncom, fn, *args):
    with patch.dict("sys.modules", {"pythoncom": pythoncom}):
        return fn(*args)


def engine_records(controller):
    """The per-entry discovery records of the last run, by ROT name."""
    return {r["rot_entry"]: r for r in controller._last_discovery["engines"]}


# ---------------------------------------------------------------------------
# Stale cached engine: dropped, re-resolved, recorded
# ---------------------------------------------------------------------------

def test_a_stale_cached_engine_is_dropped_rebuilt_and_recorded():
    conn = FakeConnection("/app/con[0]", [FakeSession("/app/con[0]/ses[0]")])
    old_engine = FakeEngine([conn])
    engines = {"SAPGUI": old_engine}
    controller, com, pythoncom = make_controller(engines, rot_names=["SAPGUI"])

    first = run(pythoncom, controller.list_connections)
    assert first[0]["session_count"] == 1
    record = engine_records(controller)["SAPGUI"]
    assert (record["cache_hit"], record["dropped_stale"],
            record["resolve_failed"]) == (False, False, False)

    # SAP Logon restarted: the cached proxy is dead, the ROT holds a new one.
    old_engine.fail_probe = True
    new_engine = FakeEngine(
        [FakeConnection("/app/con[0]", [FakeSession("/app/con[0]/ses[0]")])]
    )
    engines["SAPGUI"] = new_engine

    second = run(pythoncom, controller.list_connections)

    assert second[0]["session_count"] == 1
    assert com.lookups == ["SAPGUI", "SAPGUI"]  # re-resolved after the drop
    record = engine_records(controller)["SAPGUI"]
    assert record["dropped_stale"] is True
    assert record["cache_hit"] is False
    assert record["resolve_failed"] is False
    # The discard is visible on the connection itself, not only in the log.
    assert second[0]["discovery"]["engines"] == [
        {"host": "saplogon", "rot_entry": "SAPGUI", "cache_hit": False,
         "dropped_stale": True, "resolve_failed": False},
    ]

    # The rebuilt engine is cached again: no third lookup, a cache hit.
    run(pythoncom, controller.list_connections)
    assert com.lookups == ["SAPGUI", "SAPGUI"]
    assert engine_records(controller)["SAPGUI"]["cache_hit"] is True


def test_an_entry_that_cannot_be_resolved_records_resolve_failed():
    engines = {
        "SAPGUI": FakeEngine([]),
        "SAPGUISERVER-9B54": RuntimeError("Server execution failed"),
        "SAPGUISERVER-EA98": FakeEngine(
            [FakeConnection("/app/con[0]", [FakeSession("s")], description="B")]
        ),
    }
    controller, _com, pythoncom = make_controller(
        engines, rot_names=["SAPGUI", "SAPGUISERVER-9B54", "SAPGUISERVER-EA98"],
    )

    result = run(pythoncom, controller.list_connections)

    assert [c["rot_entry"] for c in result] == ["SAPGUISERVER-EA98"]
    records = engine_records(controller)
    assert records["SAPGUISERVER-9B54"]["resolve_failed"] is True
    assert records["SAPGUISERVER-9B54"]["host"] == "sapguiserver"
    assert records["SAPGUISERVER-EA98"]["resolve_failed"] is False


# ---------------------------------------------------------------------------
# Unsuffixed SAPGUISERVER only without suffixed entries
# ---------------------------------------------------------------------------

def test_unsuffixed_entry_is_ignored_when_suffixed_entries_exist():
    conn_a = FakeConnection("/app/con[0]", [FakeSession("s-a")], description="A")
    conn_b = FakeConnection("/app/con[0]", [FakeSession("s-b")], description="B")
    controller, com, pythoncom = make_controller(
        {
            "SAPGUISERVER": FakeEngine([conn_a]),
            "SAPGUISERVER-9B54": FakeEngine([conn_a]),
            "SAPGUISERVER-EA98": FakeEngine([conn_b]),
        },
        rot_names=["SAPGUISERVER-EA98", "SAPGUISERVER", "SAPGUISERVER-9B54"],
    )

    result = run(pythoncom, controller.list_connections)

    assert [c["rot_entry"] for c in result] == [
        "SAPGUISERVER-9B54", "SAPGUISERVER-EA98",
    ]
    # The alias is never resolved and gets no discovery record.
    assert "SAPGUISERVER" not in com.lookups
    assert "SAPGUISERVER" not in engine_records(controller)


def test_unsuffixed_entry_is_used_when_no_suffixed_entry_exists():
    conn = FakeConnection("/app/con[0]", [FakeSession("s")], description="A")
    controller, _com, pythoncom = make_controller(
        {"SAPGUISERVER": FakeEngine([conn])}, rot_names=["SAPGUISERVER"],
    )

    result = run(pythoncom, controller.list_connections)

    assert len(result) == 1
    assert result[0]["rot_entry"] == "SAPGUISERVER"
    assert result[0]["host"] == "sapguiserver"
    record = engine_records(controller)["SAPGUISERVER"]
    assert record["host"] == "sapguiserver"
    assert record["resolve_failed"] is False


# ---------------------------------------------------------------------------
# ROT enumeration failure: SAP Logon only, with a marker
# ---------------------------------------------------------------------------

def test_rot_enumeration_failure_falls_back_to_saplogon_with_marker():
    conn = FakeConnection("/app/con[0]", [FakeSession("s")], description="DEV")
    controller, _com, pythoncom = make_controller(
        {"SAPGUI": FakeEngine([conn])},
        enum_error=RuntimeError("ROT unavailable"),
    )

    result = run(pythoncom, controller.list_connections)

    assert [c["rot_entry"] for c in result] == ["SAPGUI"]
    discovery = result[0]["discovery"]
    assert discovery["rot_enum_failed"] is True
    assert "only SAP Logon" in discovery["note"]
    # Only the SAP Logon entry was tried; no sapguiserver record exists.
    assert [r["rot_entry"] for r in discovery["engines"]] == ["SAPGUI"]


def test_a_normal_run_is_marked_rot_enum_failed_false():
    conn = FakeConnection("/app/con[0]", [FakeSession("s")], description="DEV")
    controller, _com, pythoncom = make_controller(
        {"SAPGUI": FakeEngine([conn])}, rot_names=["SAPGUI"],
    )

    result = run(pythoncom, controller.list_connections)

    assert result[0]["discovery"]["rot_enum_failed"] is False
    assert "note" not in result[0]["discovery"]


# ---------------------------------------------------------------------------
# Attach boundaries: out of range, no session, scripting disabled
# ---------------------------------------------------------------------------

def _controller_with(connections, rot_names=("SAPGUI",)):
    controller, _com, pythoncom = make_controller(
        {"SAPGUI": FakeEngine(connections)}, rot_names=list(rot_names),
    )
    return controller, pythoncom


def test_connection_index_out_of_range_lists_what_is_open():
    controller, pythoncom = _controller_with([
        FakeConnection("/app/con[0]", [FakeSession("s")], description="DEV"),
    ])

    with pytest.raises(SAPGUIError, match="Connection index 3 out of range") as exc:
        run(pythoncom, controller.connect_to_existing_session, 3, 0)

    assert "Found 1 connection(s)" in str(exc.value)
    assert "0: DEV [saplogon]" in str(exc.value)
    assert controller.is_connected is False


def test_session_index_out_of_range_lists_the_available_range():
    controller, pythoncom = _controller_with([
        FakeConnection("/app/con[0]", [FakeSession("s")], description="DEV"),
    ])

    with pytest.raises(SAPGUIError,
                       match=r"Session index 5 out of range\. Available: 0-0"):
        run(pythoncom, controller.connect_to_existing_session, 0, 5)

    assert controller.is_connected is False


def test_a_connection_without_sessions_is_refused():
    controller, pythoncom = _controller_with([
        FakeConnection("/app/con[0]", [], description="DEV"),
    ])

    with pytest.raises(SAPGUIError,
                       match="Connection 0 has no open sessions"):
        run(pythoncom, controller.connect_to_existing_session, 0, 0)

    assert controller.is_connected is False


def test_a_scripting_disabled_connection_is_refused_with_the_reason():
    controller, pythoncom = _controller_with([
        FakeConnection("/app/con[0]", [FakeSession("s")], description="DEV",
                       disabled=True),
    ])

    with pytest.raises(SAPGUIError, match="sapgui/user_scripting") as exc:
        run(pythoncom, controller.connect_to_existing_session, 0, 0)

    assert "disabled on this server" in str(exc.value)
    assert controller.is_connected is False
    # The same connection is listed with the disabled note, not hidden.
    listed = run(pythoncom, controller.list_connections)
    assert listed[0]["scripting_disabled"] is True
    assert "sapgui/user_scripting" in listed[0]["note"]
