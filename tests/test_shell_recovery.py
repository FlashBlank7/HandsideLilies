from __future__ import annotations

import json

import pytest

from lilies.core import shell
from lilies.core.database import Database
from lilies.watchdog import watch


class _FakeWatchdog:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.waited = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


def test_login_shell_requires_health_path_and_literal_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.EmergencyHotkey, "start", lambda _self: None)
    written: list[str] = []
    monkeypatch.setattr(shell, "_write_login_shell", written.append)
    controller = shell.ShellController(Database(tmp_path / "test.db"), tmp_path, smoke=True)
    controller.mode = "visual"
    controller.backup_path.write_text(
        json.dumps({"taskbarVisible": True, "desktopIconsVisible": True, "loginShell": "explorer.exe"}),
        "utf-8",
    )
    with pytest.raises(PermissionError):
        controller.enable_login_shell("LiliesInTheBox.exe --login-shell", "wrong")
    controller.enable_login_shell("LiliesInTheBox.exe --login-shell", "ENABLE_LILIES_LOGIN_SHELL")
    assert written == ['LiliesInTheBox.exe --login-shell, explorer.exe']


def test_watchdog_restores_unclean_exit_but_not_clean_exit(tmp_path, monkeypatch):
    restored: list[object] = []
    monkeypatch.setattr("lilies.watchdog._alive", lambda _pid: False)
    monkeypatch.setattr("lilies.watchdog.restore_from_backup", restored.append)
    backup = tmp_path / "backup.json"
    backup.write_text("{}", "utf-8")
    assert watch(12345, backup, tmp_path / "missing.clean") == 0
    assert restored == [backup]
    restored.clear()
    marker = tmp_path / "clean.marker"
    marker.write_text("clean", "utf-8")
    assert watch(12345, backup, marker) == 0
    assert restored == []
    assert not marker.exists()


def test_recovery_monitor_can_be_prepared_without_switching_shell_mode(tmp_path):
    controller = shell.ShellController(Database(tmp_path / "test.db"), tmp_path, smoke=True)
    initial_mode = controller.mode
    controller.ensure_recovery_monitor()
    assert controller.backup_path.is_file()
    assert controller.mode == initial_mode
    controller.shutdown()


def test_recovery_monitor_reaps_and_rearms_an_exited_child(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.EmergencyHotkey, "start", lambda _self: None)
    database = Database(tmp_path / "test.db")
    controller = shell.ShellController(database, tmp_path, smoke=False)
    controller.backup_path.write_text("{}", "utf-8")
    monkeypatch.setattr(controller, "restore_visual_state", lambda: None)
    clock = [100.0]
    spawned: list[_FakeWatchdog] = []

    def spawn(_command, creationflags=0):
        child = _FakeWatchdog()
        spawned.append(child)
        return child

    monkeypatch.setattr(shell.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(shell.subprocess, "Popen", spawn)
    try:
        controller.ensure_recovery_monitor()
        assert len(spawned) == 1
        spawned[0].returncode = 7

        # First maintenance observes the rapid exit and applies backoff.
        assert controller.maintain_recovery_monitor() is False
        assert spawned[0].waited is True
        assert len(spawned) == 1
        assert controller._watchdog_marker is None

        clock[0] += 1.0
        assert controller.maintain_recovery_monitor() is True
        assert len(spawned) == 2
        assert controller.health_check()["watchdogRunning"] is True
    finally:
        controller.shutdown()


def test_compact_shell_does_not_arm_recovery_monitor_from_periodic_maintenance(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(shell.EmergencyHotkey, "start", lambda _self: None)
    controller = shell.ShellController(Database(tmp_path / "test.db"), tmp_path, smoke=False)
    controller.backup_path.write_text("{}", "utf-8")
    monkeypatch.setattr(controller, "restore_visual_state", lambda: None)
    controller.mode = "compact"
    spawned: list[object] = []
    monkeypatch.setattr(
        shell.subprocess,
        "Popen",
        lambda *_args, **_kwargs: spawned.append(object()),
    )
    try:
        assert controller.maintain_recovery_monitor() is False
        assert spawned == []
    finally:
        controller.shutdown()


def test_visual_and_compact_modes_round_trip_and_persist(tmp_path):
    database_path = tmp_path / "test.db"
    controller = shell.ShellController(Database(database_path), tmp_path, smoke=True)

    controller.enter_compact()
    assert controller.mode == "compact"
    assert controller.database.get_setting("shell_mode") == "compact"
    controller.shutdown()

    restarted = shell.ShellController(Database(database_path), tmp_path, smoke=True)
    assert restarted.mode == "compact"
    restarted.enter_visual()
    assert restarted.mode == "visual"
    assert restarted.database.get_setting("shell_mode") == "visual"
    restarted.shutdown()

    final_restart = shell.ShellController(Database(database_path), tmp_path, smoke=True)
    assert final_restart.mode == "visual"
    final_restart.shutdown()


def test_low_level_emergency_restore_does_not_replace_next_launch_preference(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "test.db")
    controller = shell.ShellController(database, tmp_path, smoke=True)
    controller.enter_visual()
    restored: list[str] = []
    disabled: list[str] = []
    monkeypatch.setattr(
        controller, "restore_visual_state", lambda: restored.append("windows")
    )
    monkeypatch.setattr(
        controller, "disable_login_shell", lambda: disabled.append("login-shell")
    )

    controller.emergency_restore()

    assert restored == ["windows"]
    assert disabled == ["login-shell"]
    assert controller.mode == "visual"
    assert database.get_setting("shell_mode") == "visual"
    controller.shutdown()
