from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lilies import watchdog
from lilies.core import recovery, shell


@pytest.fixture(autouse=True)
def forbid_real_recovery(monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("test must not access native recovery")
    monkeypatch.setattr(shell, "_user32", forbidden)
    monkeypatch.setattr(recovery, "recover_desktop_peek", forbidden)


@pytest.mark.parametrize("shell_result,peek_result,ok", [
    ({"ok": True}, {"active": False, "failed": 0}, True),
    ({"ok": False}, {"active": False, "failed": 0}, False),
    (None, {"active": False, "failed": 0}, False),
    ({"ok": "yes"}, {"active": False, "failed": 0}, False),
    ({"ok": True}, {"active": True, "failed": 1}, False),
    ({"ok": True}, {"active": False, "failed": False}, False),
    ({"ok": True}, {"active": 0, "failed": 0}, False),
    ({"ok": True}, None, False),
])
def test_combined_result_requires_explicit_success(tmp_path, monkeypatch, shell_result, peek_result, ok):
    calls = []
    def peek(path):
        calls.append(path)
        return peek_result
    monkeypatch.setattr(recovery, "recover_desktop_peek", peek)
    result = recovery.finish_recovery_report(shell_result, tmp_path)
    assert result["ok"] is ok
    assert calls == [tmp_path]  # Attempt window recovery even when shell recovery fails.


@pytest.mark.parametrize("exception", [OSError, RuntimeError, ValueError])
def test_peek_failure_keeps_existing_journal_and_reports_no_content(tmp_path, monkeypatch, exception):
    journal = tmp_path / "synthetic-journal.json"
    journal.write_text("sensitive-window-title", "utf-8")
    def failed(_):
        raise exception("sensitive-window-title")
    monkeypatch.setattr(recovery, "recover_desktop_peek", failed)
    result = recovery.finish_recovery_report({"ok": True}, tmp_path)
    assert not result["ok"] and result["errors"] == ["peek-recovery-failed"]
    assert "sensitive" not in json.dumps(result)
    assert journal.read_text("utf-8") == "sensitive-window-title"


def test_unavailable_data_root_does_not_read_peek_journal():
    result = recovery.finish_recovery_report({"ok": False}, None)
    assert not result["ok"] and "peek-storage-unavailable" in result["errors"]


@pytest.mark.parametrize("shell_ok,peek_ok", [(True, True), (False, True), (True, False), (False, False)])
def test_cli_exit_status_combines_both_phases(tmp_path, monkeypatch, capsys, shell_ok, peek_ok):
    from lilies import app
    calls = []
    def restore(path, **kwargs):
        calls.append((path, kwargs))
        return {"ok": shell_ok}
    monkeypatch.setattr(app, "data_root", lambda: tmp_path)
    monkeypatch.setattr(app, "restore_from_backup", restore)
    monkeypatch.setattr(recovery, "recover_desktop_peek", lambda _: {"active": not peek_ok, "failed": 0 if peek_ok else 1})
    assert app.main(["--restore"]) == (0 if shell_ok and peek_ok else 4)
    assert calls == [(tmp_path / "shell-backup.json", {"wait_seconds": 8.0})]
    assert json.loads(capsys.readouterr().out)["ok"] is (shell_ok and peek_ok)


def test_cli_unavailable_fixed_root_has_no_fallback_disk_access(tmp_path, monkeypatch, capsys):
    from lilies import app
    from lilies.paths import DataRootUnavailableError
    def unavailable():
        raise DataRootUnavailableError(tmp_path, "test unavailable")
    calls = []
    def restore(path, **_):
        calls.append(path)
        return {"ok": False}
    monkeypatch.setattr(app, "data_root", unavailable)
    monkeypatch.setattr(app, "restore_from_backup", restore)
    assert app.main(["--restore"]) == 4
    assert calls == [None]
    assert json.loads(capsys.readouterr().out)["desktopPeekRestored"] is False


def test_windowless_cli_has_no_stdout_requirement(tmp_path, monkeypatch):
    from lilies import app
    monkeypatch.setattr(app, "data_root", lambda: tmp_path)
    monkeypatch.setattr(app, "restore_from_backup", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(recovery, "recover_desktop_peek", lambda _: {"active": False, "failed": 0})
    monkeypatch.setattr(sys, "stdout", None)
    assert app.main(["--restore"]) == 0


@pytest.mark.parametrize("marker_text", [None, "wrong", "clean"])
@pytest.mark.parametrize("peek_ok", [False, True])
def test_watchdog_restores_unclean_shell_and_always_recovers_peek(tmp_path, monkeypatch, marker_text, peek_ok):
    marker = tmp_path / "owned.clean"
    if marker_text is not None:
        marker.write_text(marker_text, "utf-8")
    monkeypatch.setattr(watchdog, "_alive", lambda _: False)
    restored = []
    def restore(path, **kwargs):
        restored.append((path, kwargs))
        return {"ok": True}
    monkeypatch.setattr(watchdog, "restore_from_backup", restore)
    monkeypatch.setattr(recovery, "recover_desktop_peek", lambda _: {"active": not peek_ok, "failed": int(not peek_ok)})
    assert watchdog.watch(123, tmp_path / "backup.json", marker, tmp_path) == (0 if peek_ok else 4)
    assert bool(restored) is (marker_text != "clean")
    if restored:
        assert restored[0][1] == {"wait_seconds": 8.0}
    if marker_text == "clean":
        assert not marker.exists()


def test_watchdog_propagates_unverified_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "_alive", lambda _: False)
    monkeypatch.setattr(watchdog, "restore_from_backup", lambda *_a, **_k: {"ok": False})
    monkeypatch.setattr(recovery, "recover_desktop_peek", lambda _: {"active": False, "failed": 0})
    assert watchdog.watch(123, tmp_path / "backup", tmp_path / "marker", tmp_path) == 4


@pytest.mark.parametrize("error", [0, 5, 87])
def test_liveness_error_is_not_mistaken_for_process_exit(monkeypatch, error):
    monkeypatch.setattr(watchdog, "_kernel32", lambda: SimpleNamespace(OpenProcess=lambda *_: 0))
    monkeypatch.setattr(watchdog.ctypes, "get_last_error", lambda: error)
    if error == 87:
        assert watchdog._alive(123) is False
    else:
        with pytest.raises(OSError):
            watchdog._alive(123)


@pytest.mark.parametrize("result", [0, 258, 0xFFFFFFFF, 128])
def test_liveness_preserves_64bit_handle_and_always_closes(monkeypatch, result):
    handle = 0x12345678ABCD
    seen, closed = [], []
    def wait(value, timeout):
        seen.append((value, timeout))
        return result
    monkeypatch.setattr(watchdog, "_kernel32", lambda: SimpleNamespace(
        OpenProcess=lambda *_: handle, WaitForSingleObject=wait, CloseHandle=closed.append))
    if result in {0, 258}:
        assert watchdog._alive(123) is (result == 258)
    else:
        with pytest.raises(OSError):
            watchdog._alive(123)
    assert seen == [(handle, 0)] and closed == [handle]


def test_restore_wrapper_checks_exit_status_before_claiming_success():
    script = (Path(__file__).parents[1] / "scripts" / "restore_windows.ps1").read_text("utf-8")
    assert script.index("$restoreExitCode = $LASTEXITCODE") < script.index("recovery state verified")
    assert "exit $restoreExitCode" in script


@pytest.mark.parametrize("pid", [0, -1, True, 0x100000000])
def test_invalid_watch_pid_cannot_trigger_recovery(pid, monkeypatch):
    def forbidden():
        raise AssertionError("invalid pid reached system")
    monkeypatch.setattr(watchdog, "_kernel32", forbidden)
    with pytest.raises(ValueError):
        watchdog._alive(pid)


@pytest.mark.skipif(os.name != "nt", reason="Windows process handle contract")
def test_native_liveness_only_against_own_hidden_fixture_child():
    # Not Lilies, Explorer, or a user process; no GUI, files, model or account.
    child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert watchdog._alive(child.pid) is True
        child.terminate()
        child.wait(timeout=10)
        assert watchdog._alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
