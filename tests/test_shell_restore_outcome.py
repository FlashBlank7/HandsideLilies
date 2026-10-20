from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lilies.core import shell

_READ_LOGIN_SHELL = shell._read_login_shell_state


@pytest.fixture
def backup(tmp_path):
    path = tmp_path / "shell-backup.json"
    path.write_text(json.dumps({"taskbarVisible": True, "desktopIconsVisible": True,
                                "loginShell": "explorer.exe"}), "utf-8")
    return path


@pytest.fixture(autouse=True)
def fake_shell(monkeypatch):
    # Even the pre-fix implementation must never touch the user's desktop.
    state = SimpleNamespace(primary=11, secondary=12, view=13, visible={11: True, 12: True, 13: True},
                            registry={"present": True, "type": 1, "value": "LiliesInTheBox.exe"},
                            clock=100.0, sleeps=[], commands=[], writes=[], launches=0,
                            apply=True, launch_error=False, appear_at=None)

    def find(_parent, previous, name, _title):
        if name == "SHELLDLL_DefView":
            return state.view
        return state.secondary if name == "Shell_SecondaryTrayWnd" and not previous else 0

    def show(hwnd, command):
        state.commands.append((hwnd, command))
        if state.apply:
            state.visible[hwnd] = command != 0
        return True

    def write(value):
        state.writes.append(value)
        state.registry = {"present": True, "type": 1, "value": value}

    def start():
        state.launches += 1
        if state.launch_error:
            raise OSError("synthetic explorer start failed")

    def sleep(duration):
        state.sleeps.append(duration)
        state.clock += duration
        if state.appear_at is not None and state.clock >= state.appear_at:
            state.primary, state.view = 11, 13

    user32 = SimpleNamespace(FindWindowW=lambda name, *_: state.primary if name == "Shell_TrayWnd" else 20,
                             FindWindowExW=find, IsWindowVisible=lambda hwnd: state.visible[hwnd],
                             ShowWindowAsync=show)
    monkeypatch.setattr(shell, "_user32", lambda: user32)
    monkeypatch.setattr(shell.ctypes.windll, "user32", user32)
    monkeypatch.setattr(shell, "_read_login_shell_state", lambda: deepcopy(state.registry))
    monkeypatch.setattr(shell, "_write_login_shell", write)
    monkeypatch.setattr(shell, "_start_explorer", start)
    monkeypatch.setattr(shell, "time", SimpleNamespace(monotonic=lambda: state.clock, sleep=sleep))
    monkeypatch.setattr(shell.subprocess, "Popen", lambda *_a, **_k: None)
    return state


def test_restore_reports_registry_failure_instead_of_silent_success(backup, monkeypatch):
    def denied(*_):
        raise PermissionError("synthetic denied")

    monkeypatch.setattr(shell, "_write_login_shell", denied)
    result = shell.restore_from_backup(backup)
    assert isinstance(result, dict), "recovery needs an observable outcome"
    assert result["ok"] is False


def test_malformed_backup_reports_failure_instead_of_crashing(backup):
    backup.write_text("[]", "utf-8")
    result = shell.restore_from_backup(backup)
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["backupStatus"] == "invalid"


def test_restore_cli_does_not_return_success_for_failed_recovery(tmp_path, monkeypatch):
    from lilies import app
    from lilies.core import recovery

    monkeypatch.setattr(app, "data_root", lambda: tmp_path)
    monkeypatch.setattr(app, "restore_from_backup", lambda *_a, **_k: {"ok": False})
    monkeypatch.setattr(recovery, "recover_desktop_peek", lambda *_: {"active": False, "failed": 0})
    assert app.main(["--restore"]) != 0


def test_success_requires_readback_of_all_restored_states(backup, fake_shell):
    fake_shell.visible = {11: False, 12: False, 13: False}
    result = shell.restore_from_backup(backup)
    assert result["ok"] is True
    assert result["backupStatus"] == "legacy"
    assert result["errors"] == []
    assert fake_shell.visible == {11: True, 12: True, 13: True}
    assert fake_shell.writes == ["explorer.exe"]
    assert fake_shell.sleeps == []
    assert "explorer.exe" not in json.dumps(result)


def test_accepted_async_request_does_not_prove_visibility(backup, fake_shell):
    fake_shell.apply = False
    fake_shell.visible[12] = False
    result = shell.restore_from_backup(backup, wait_seconds=0.3)
    assert result["ok"] is False
    assert result["taskbarVisibilityMatched"] is False
    assert result["desktopIconsVisibilityMatched"] is True
    assert "taskbar-state-unverified" in result["errors"]
    assert len(fake_shell.sleeps) <= 4


def test_delayed_explorer_is_reenumerated_then_shown(backup, fake_shell):
    fake_shell.primary, fake_shell.view = 0, 0
    fake_shell.visible[11] = fake_shell.visible[13] = False
    fake_shell.appear_at = 100.2
    result = shell.restore_from_backup(backup, wait_seconds=1.0)
    assert result["ok"] is True
    assert fake_shell.launches == 1
    assert result["explorerStartRequested"] is True
    assert fake_shell.visible[11] and fake_shell.visible[13]


@pytest.mark.parametrize("launch_error", [False, True])
def test_missing_explorer_never_reports_success_or_launches_repeatedly(backup, fake_shell, launch_error):
    fake_shell.primary, fake_shell.view = 0, 0
    fake_shell.launch_error = launch_error
    result = shell.restore_from_backup(backup, wait_seconds=100.0)
    assert result["ok"] is False
    assert fake_shell.launches == 1
    assert fake_shell.clock <= 108.001


def test_gui_default_does_not_wait_for_explorer(backup, fake_shell):
    fake_shell.primary, fake_shell.view = 0, 0
    assert shell.restore_from_backup(backup)["ok"] is False
    assert fake_shell.sleeps == []


@pytest.mark.parametrize("content", ["null", "[]", '"wrong"', '{}', '{"taskbarVisible":"false"}',
    '{"taskbarVisible":true,"desktopIconsVisible":true,"loginShell":[]}',
    '{"schemaVersion":3,"taskbarVisible":true,"desktopIconsVisible":true,"loginShell":"explorer.exe"}',
    '{"schemaVersion":2,"taskbarVisible":true,"desktopIconsVisible":true,"loginShellState":{"present":0}}',
    "{" + " " * 65536 + "}"], ids=["null", "list", "string", "partial", "string-bool",
                                     "invalid-shell", "future-schema", "numeric-present", "oversize"])
def test_invalid_backup_uses_visual_fallback_without_overwriting_registry(backup, fake_shell, content):
    backup.write_text(content, "utf-8")
    original = deepcopy(fake_shell.registry)
    result = shell.restore_from_backup(backup)
    assert result["ok"] is False and result["backupStatus"] == "invalid"
    assert fake_shell.writes == [] and fake_shell.registry == original
    assert result["taskbarVisibilityMatched"] is True
    assert backup.read_text("utf-8") == content


@pytest.mark.parametrize("case", ["missing", "unavailable", "unreadable"])
def test_unknown_backup_is_not_exact_recovery(tmp_path, fake_shell, case):
    path = None if case == "unavailable" else tmp_path / "not-a-backup"
    if case == "unreadable":
        path.mkdir()
    result = shell.restore_from_backup(path)
    assert not result["ok"] and result["backupStatus"] == case
    assert fake_shell.writes == []


def test_hidden_original_visibility_is_preserved(backup, fake_shell):
    backup.write_text(json.dumps({"taskbarVisible": False, "desktopIconsVisible": False,
                                 "loginShell": "explorer.exe"}), "utf-8")
    result = shell.restore_from_backup(backup)
    assert result["ok"] is True
    assert not any(fake_shell.visible.values())


def test_registry_mismatch_after_write_is_failure(backup, monkeypatch):
    monkeypatch.setattr(shell, "_write_login_shell", lambda *_: None)
    result = shell.restore_from_backup(backup)
    assert not result["ok"] and not result["loginShellMatched"]


def test_validated_absent_override_is_restored_as_absent(backup, monkeypatch, fake_shell):
    backup.write_text(json.dumps({"schemaVersion": 2, "taskbarVisible": True,
        "desktopIconsVisible": True, "loginShellState": {"present": False}}), "utf-8")
    removed = []
    class Key:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    def delete(_key, name):
        removed.append(name)
        fake_shell.registry = {"present": False}
    import sys
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2,
                         OpenKey=lambda *_: Key(), DeleteValue=delete))
    assert shell.restore_from_backup(backup)["ok"] is True
    assert removed == ["Shell"] and fake_shell.writes == []


@pytest.mark.parametrize("wait", [float("inf"), float("nan"), "8"])
def test_invalid_wait_rejected_before_requests(backup, fake_shell, wait):
    with pytest.raises(ValueError):
        shell.restore_from_backup(backup, wait_seconds=wait)
    assert fake_shell.commands == [] and fake_shell.writes == []


def test_repeating_native_enumeration_fails_closed(backup, monkeypatch, fake_shell):
    api = shell._user32()
    api.FindWindowExW = lambda *_: 12
    result = shell.restore_from_backup(backup)
    assert not result["ok"] and "visual-state-read-failed" in result["errors"]
    assert fake_shell.sleeps == []


def test_smoke_shutdown_never_reads_or_mutates_windows(tmp_path, monkeypatch):
    from lilies.core.database import Database
    def forbidden(*_a, **_k):
        raise AssertionError("diagnostic touched Windows")
    monkeypatch.setattr(shell, "_user32", forbidden)
    monkeypatch.setattr(shell, "_read_login_shell_state", forbidden)
    monkeypatch.setattr(shell, "restore_from_backup", forbidden)
    controller = shell.ShellController(Database(tmp_path / "db.sqlite"), tmp_path, smoke=True)
    controller.enter_visual()
    controller.enter_compact()
    controller.shutdown()
    assert controller.status()["lastRestore"]["backupStatus"] == "diagnostic-skipped"


@pytest.mark.parametrize("outcome", [None, {"ok": False}, {"ok": "true"}, {"ok": True}])
def test_clean_marker_requires_verified_result(tmp_path, monkeypatch, outcome):
    from lilies.core.database import Database
    controller = shell.ShellController(Database(tmp_path / "db.sqlite"), tmp_path, smoke=True)
    controller._watchdog_marker = tmp_path / "owned.clean"
    monkeypatch.setattr(controller, "restore_visual_state", lambda: outcome)
    controller.shutdown()
    assert controller._watchdog_marker.exists() is (isinstance(outcome, dict) and outcome.get("ok") is True)


@pytest.mark.parametrize("missing_at", ["key", "value"])
def test_registry_missing_is_distinct_from_denied(monkeypatch, missing_at):
    import sys
    class Key:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    def open_key(*_):
        if missing_at == "key":
            raise FileNotFoundError()
        return Key()
    def query(*_):
        raise FileNotFoundError()
    registry = SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=open_key, QueryValueEx=query)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert _READ_LOGIN_SHELL() == {"present": False}
    def denied(*_):
        raise PermissionError("do not leak this")
    registry.OpenKey = denied
    with pytest.raises(PermissionError):
        _READ_LOGIN_SHELL()


def test_backup_denied_or_corrupt_prevents_hiding_desktop(tmp_path, monkeypatch, fake_shell):
    from lilies.core.database import Database
    monkeypatch.setattr(shell.EmergencyHotkey, "start", lambda _: None)
    controller = shell.ShellController(Database(tmp_path / "db.sqlite"), tmp_path)
    started = []
    monkeypatch.setattr(controller, "_start_watchdog", lambda **_: started.append(True))
    def denied():
        raise PermissionError("fake registry denied")
    monkeypatch.setattr(shell, "_read_login_shell_state", denied)
    with pytest.raises(PermissionError):
        controller.enter_visual()
    assert not controller.backup_path.exists()
    assert fake_shell.commands == [] and started == []
    controller.backup_path.write_text("[]", "utf-8")
    with pytest.raises(RuntimeError):
        controller.enter_visual()
    assert fake_shell.commands == [] and started == []


@pytest.mark.parametrize("original", [{"present": False},
    {"present": True, "type": 2, "value": "%SystemRoot%\\explorer.exe"}])
def test_capture_preserves_absence_and_registry_type(tmp_path, monkeypatch, fake_shell, original):
    from lilies.core.database import Database
    monkeypatch.setattr(shell.EmergencyHotkey, "start", lambda _: None)
    fake_shell.registry = original
    controller = shell.ShellController(Database(tmp_path / "db.sqlite"), tmp_path)
    controller._ensure_backup()
    saved = json.loads(controller.backup_path.read_text("utf-8"))
    assert saved["schemaVersion"] == 2 and saved["loginShellState"] == original
    assert fake_shell.commands == [] and fake_shell.writes == []


def test_expanded_registry_string_restores_original_type(backup, fake_shell, monkeypatch):
    import sys
    original = {"present": True, "type": 2, "value": "%SystemRoot%\\explorer.exe"}
    backup.write_text(json.dumps({"schemaVersion": 2, "taskbarVisible": True,
        "desktopIconsVisible": True, "loginShellState": original}), "utf-8")
    class Key:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    writes = []
    def set_value(_key, name, _reserved, kind, value):
        writes.append((name, kind, value))
        fake_shell.registry = {"present": True, "type": kind, "value": value}
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(HKEY_CURRENT_USER=1,
        CreateKey=lambda *_: Key(), SetValueEx=set_value))
    assert shell.restore_from_backup(backup)["ok"] is True
    assert writes == [("Shell", 2, original["value"])]


def test_readback_exception_is_failure_and_has_no_private_error_text(backup, monkeypatch):
    def denied():
        raise PermissionError("private shell command")
    monkeypatch.setattr(shell, "_read_login_shell_state", denied)
    result = shell.restore_from_backup(backup)
    assert not result["ok"] and not result["loginShellMatched"]
    assert "private" not in json.dumps(result)
