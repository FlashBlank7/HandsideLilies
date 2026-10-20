from __future__ import annotations

import ctypes
import json
import math
import os
import secrets
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .database import Database, utc_now


SW_HIDE = 0
SW_SHOW = 5
WM_CLOSE = 0x0010
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_F12 = 0x7B
PROGMAN_SPAWN_WORKERW = 0x052C
WINLOGON_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"


@lru_cache(maxsize=1)
def _user32():
    if os.name != "nt":
        return None
    api = ctypes.WinDLL("user32", use_last_error=True)
    for name, arguments, result in (
        ("FindWindowW", [wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND),
        ("FindWindowExW", [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND),
        ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
        ("ShowWindowAsync", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
    ):
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _find_defview() -> int:
    user32 = _user32()
    if user32 is None:
        return 0
    progman = user32.FindWindowW("Progman", None)
    view = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None) if progman else 0
    if view:
        return int(view)
    result = 0
    worker = 0
    seen: set[int] = set()
    for _ in range(256):
        worker = user32.FindWindowExW(0, worker, "WorkerW", None)
        if not worker:
            break
        if int(worker) in seen:
            raise OSError("desktop enumeration did not advance")
        seen.add(int(worker))
        view = user32.FindWindowExW(worker, 0, "SHELLDLL_DefView", None)
        if view:
            result = int(view)
            break
    else:
        raise OSError("desktop enumeration exceeded bound")
    return result


def _window_visible(hwnd: int) -> bool:
    user32 = _user32()
    return bool(hwnd and user32 and user32.IsWindowVisible(hwnd))


def _taskbar_handles() -> tuple[int, list[int]]:
    user32 = _user32()
    if user32 is None:
        return 0, []
    primary = int(user32.FindWindowW("Shell_TrayWnd", None) or 0)
    handles = [primary] if primary else []
    current = 0
    for _ in range(256):
        current = user32.FindWindowExW(0, current, "Shell_SecondaryTrayWnd", None)
        if not current:
            return primary, handles
        if int(current) in handles:
            raise OSError("taskbar enumeration did not advance")
        handles.append(int(current))
    raise OSError("taskbar enumeration exceeded bound")


def _show_taskbars(show: bool) -> None:
    user32 = _user32()
    if user32 is not None:
        for hwnd in _taskbar_handles()[1]:
            # Explorer may be unresponsive. Posting does not block Qt while
            # waiting for its thread; success is verified separately below.
            user32.ShowWindowAsync(hwnd, SW_SHOW if show else SW_HIDE)


def _show_desktop_icons(show: bool) -> None:
    user32 = _user32()
    view = _find_defview()
    if user32 is not None and view:
        user32.ShowWindowAsync(view, SW_SHOW if show else SW_HIDE)


def _read_login_shell() -> str:
    state = _read_login_shell_state()
    return str(state["value"]) if state["present"] else "explorer.exe"


def _read_login_shell_state() -> dict[str, object]:
    if os.name != "nt":
        return {"present": False}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINLOGON_KEY) as key:
            value, kind = winreg.QueryValueEx(key, "Shell")
    except FileNotFoundError:
        return {"present": False}
    # Access denied is not an absent value. Never back up a guessed default.
    return _validate_login_shell_state({"present": True, "value": value, "type": kind})


def _write_login_shell(value: str) -> None:
    if os.name != "nt":
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINLOGON_KEY) as key:
        winreg.SetValueEx(key, "Shell", 0, winreg.REG_SZ, value)


def _validate_login_shell_state(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or type(value.get("present")) is not bool:
        raise ValueError("invalid login shell state")
    if not value["present"]:
        if set(value) != {"present"}:
            raise ValueError("absent shell has unexpected data")
        return {"present": False}
    if (set(value) != {"present", "type", "value"}
            or type(value["type"]) is not int or value["type"] not in {1, 2}
            or not isinstance(value["value"], str)
            or not 0 < len(value["value"]) <= 32767 or "\0" in value["value"]):
        raise ValueError("invalid login shell value")
    return dict(value)


def _restore_login_shell_state(state: dict[str, object]) -> None:
    state = _validate_login_shell_state(state)
    if state.get("present") and state["type"] == 1:
        _write_login_shell(str(state["value"]))
        return
    if os.name != "nt":
        return
    import winreg
    if state["present"]:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINLOGON_KEY) as key:
            winreg.SetValueEx(key, "Shell", 0, int(state["type"]), str(state["value"]))
    else:
        # Delete only the value Lilies may have introduced, not the key or any
        # other settings; an originally absent override must remain absent.
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINLOGON_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "Shell")
        except FileNotFoundError:
            pass


def _restore_target(path: Path | None) -> tuple[dict[str, object], str]:
    fallback: dict[str, object] = {"taskbarVisible": True, "desktopIconsVisible": True,
                                   "loginShellState": None}
    if path is None:
        return fallback, "unavailable"
    try:
        with path.open("r", encoding="utf-8") as stream:
            encoded = stream.read(65537)
        if len(encoded) > 65536:
            raise ValueError("backup too large")
        backup = json.loads(encoded)
        if (not isinstance(backup, dict)
                or type(backup.get("taskbarVisible")) is not bool
                or type(backup.get("desktopIconsVisible")) is not bool):
            raise ValueError("invalid visibility state")
        version = backup.get("schemaVersion", 1)
        if type(version) is not int or version not in {1, 2}:
            raise ValueError("unknown backup schema")
        state = _validate_login_shell_state(backup.get("loginShellState") if version == 2 else {
            "present": True, "type": 1, "value": backup.get("loginShell"),
        })
        return {"taskbarVisible": backup["taskbarVisible"],
                "desktopIconsVisible": backup["desktopIconsVisible"],
                "loginShellState": state}, "validated" if version == 2 else "legacy"
    except FileNotFoundError:
        return fallback, "missing"
    except OSError:
        return fallback, "unreadable"
    except (ValueError, UnicodeError, RecursionError):
        return fallback, "invalid"


def _start_explorer() -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel32.GetWindowsDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("Windows directory unavailable")
    subprocess.Popen([str(Path(buffer.value) / "explorer.exe")],
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)


def restore_from_backup(path: Path | None, *, wait_seconds: float = 0.0) -> dict[str, object]:
    """Request restoration and report observed state, never just API success.

    The default is a single non-waiting pass for the GUI. Only the separate
    recovery CLI/watchdog opts into bounded polling (at most eight seconds).
    Visibility means WS_VISIBLE, not proof of unobscured pixels or Z order.
    No paths, HWNDs or registry values enter the returned diagnostic.
    """
    if not isinstance(wait_seconds, (int, float)) or not math.isfinite(wait_seconds):
        raise ValueError("invalid recovery wait")
    duration = max(0.0, min(8.0, float(wait_seconds)))
    target, backup_status = _restore_target(path)
    report: dict[str, object] = {"schemaVersion": 1, "ok": False, "backupStatus": backup_status,
        "taskbarVisibilityMatched": False, "desktopIconsVisibilityMatched": False,
        "loginShellMatched": False, "explorerStartRequested": False, "errors": []}
    if _user32() is None:
        report["errors"] = ["unsupported-platform"]
        return report
    expected_shell = target["loginShellState"]
    registry_error = ""
    if expected_shell is not None:
        try:
            if _read_login_shell_state() != expected_shell:
                _restore_login_shell_state(expected_shell)
        except (OSError, ValueError):
            registry_error = "login-shell-write-or-read-failed"
    deadline = time.monotonic() + duration
    explorer_attempted = False
    for _ in range(82):
        errors = [] if backup_status in {"validated", "legacy"} else ["backup-" + backup_status]
        if registry_error:
            errors.append(registry_error)
        # Re-enumerate after every request: Explorer can restart while restoring.
        try:
            primary, _handles = _taskbar_handles()
            if not primary and not explorer_attempted:
                explorer_attempted = True
                _start_explorer()
                report["explorerStartRequested"] = True
            _show_taskbars(bool(target["taskbarVisible"]))
        except OSError:
            errors.append("taskbar-request-failed")
        try:
            _show_desktop_icons(bool(target["desktopIconsVisible"]))
        except OSError:
            errors.append("desktop-icons-request-failed")
        report["taskbarVisibilityMatched"] = False
        report["desktopIconsVisibilityMatched"] = False
        report["loginShellMatched"] = False
        try:
            primary, handles = _taskbar_handles()
            report["taskbarVisibilityMatched"] = bool(primary and all(
                _window_visible(hwnd) == target["taskbarVisible"] for hwnd in handles))
            view = _find_defview()
            report["desktopIconsVisibilityMatched"] = bool(
                view and _window_visible(view) == target["desktopIconsVisible"])
        except OSError:
            errors.append("visual-state-read-failed")
        if expected_shell is not None:
            try:
                report["loginShellMatched"] = _read_login_shell_state() == expected_shell
            except (OSError, ValueError):
                errors.append("login-shell-read-failed")
        for field, code in (("taskbarVisibilityMatched", "taskbar-state-unverified"),
                            ("desktopIconsVisibilityMatched", "desktop-icons-state-unverified"),
                            ("loginShellMatched", "login-shell-state-unverified")):
            if not report[field]:
                errors.append(code)
        report["errors"] = list(dict.fromkeys(errors))
        report["ok"] = not errors
        remaining = deadline - time.monotonic()
        if report["ok"] or remaining <= 0:
            break
        time.sleep(min(0.1, remaining))
    return report


class EmergencyHotkey:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.thread: threading.Thread | None = None
        self.thread_id = 0

    def start(self) -> None:
        if os.name != "nt" or self.thread is not None:
            return

        def run() -> None:
            user32 = ctypes.windll.user32
            self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            hotkey_id = 0x4C49
            if not user32.RegisterHotKey(None, hotkey_id, MOD_CONTROL | MOD_ALT | MOD_SHIFT, VK_F12):
                return
            message = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                    if message.message == WM_HOTKEY and message.wParam == hotkey_id:
                        self.callback()
            finally:
                user32.UnregisterHotKey(None, hotkey_id)

        self.thread = threading.Thread(target=run, name="lilies-emergency-hotkey", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if os.name == "nt" and self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


class ShellController:
    def __init__(self, database: Database, data_directory: Path, smoke: bool = False) -> None:
        self.database = database
        self.data_directory = data_directory
        self.smoke = smoke
        # Smoke skips Explorer mutations but still renders the selected shell mode.
        self.mode = str(database.get_setting("shell_mode", "visual"))
        if self.mode not in {"visual", "login", "compact"}:
            self.mode = "visual"
        self.backup_path = data_directory / "shell-backup.json"
        self._watchdog_marker: Path | None = None
        self._watchdog: subprocess.Popen | None = None
        self._watchdog_armed = False
        self._watchdog_stopping = False
        self._watchdog_started_at = 0.0
        self._watchdog_restart_failures = 0
        self._watchdog_next_restart_at = 0.0
        self._last_explorer_restart = 0.0
        self._last_restore: dict[str, object] | None = None
        self.hotkey = EmergencyHotkey(self.emergency_restore)
        if not self.smoke:
            self.hotkey.start()

    def capture(self) -> dict[str, object]:
        user32 = None if self.smoke else _user32()
        taskbar = int(user32.FindWindowW("Shell_TrayWnd", None) or 0) if user32 else 0
        view = _find_defview() if user32 else 0
        login_shell = {"present": False} if self.smoke else _read_login_shell_state()
        return {
            "schemaVersion": 2,
            "capturedAt": utc_now(),
            "taskbarVisible": _window_visible(taskbar) if taskbar else True,
            "desktopIconsVisible": _window_visible(view) if view else True,
            "loginShell": login_shell.get("value", "explorer.exe"),
            "loginShellState": login_shell,
        }

    def _ensure_backup(self) -> None:
        if not self.backup_path.exists():
            captured = self.capture()
            try:
                with self.backup_path.open("x", encoding="utf-8") as stream:
                    json.dump(captured, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
        if _restore_target(self.backup_path)[1] not in {"validated", "legacy"}:
            raise RuntimeError("恢复备份不可验证；没有隐藏 Windows 桌面")

    def _start_watchdog(self, *, honor_backoff: bool = False) -> bool:
        """Start or re-arm the independent shell recovery process.

        A ``Popen`` object can outlive its child, so merely checking that the
        attribute is non-null is not sufficient.  Reap an exited child and
        allow the periodic shell monitor to replace it.  Rapid repeated exits
        use a bounded backoff so a broken watchdog cannot create a process
        storm.
        """

        if self.smoke:
            return True
        if self._watchdog_stopping:
            return False

        now = time.monotonic()
        if self._watchdog is not None:
            if self._watchdog.poll() is None:
                if now - self._watchdog_started_at >= 30.0:
                    self._watchdog_restart_failures = 0
                    self._watchdog_next_restart_at = 0.0
                return True
            try:
                self._watchdog.wait(timeout=0)
            except (OSError, subprocess.TimeoutExpired):
                pass
            lived_for = max(0.0, now - self._watchdog_started_at)
            self._watchdog = None
            exited_marker = self._watchdog_marker
            self._watchdog_marker = None
            if exited_marker is not None:
                try:
                    exited_marker.unlink(missing_ok=True)
                except OSError:
                    pass
            if lived_for < 30.0:
                self._watchdog_restart_failures = min(
                    self._watchdog_restart_failures + 1,
                    6,
                )
                delay = min(30.0, float(2 ** (self._watchdog_restart_failures - 1)))
                self._watchdog_next_restart_at = max(
                    self._watchdog_next_restart_at,
                    now + delay,
                )
            else:
                self._watchdog_restart_failures = 0
                self._watchdog_next_restart_at = 0.0

        if honor_backoff and now < self._watchdog_next_restart_at:
            return False
        marker = self.data_directory / f"watchdog-{os.getpid()}-{secrets.token_hex(4)}.clean"
        if getattr(sys, "frozen", False):
            command = [
                sys.executable,
                "--watchdog",
                str(os.getpid()),
                str(self.backup_path),
                str(marker),
                "--peek-data-dir",
                str(self.data_directory),
            ]
        else:
            command = [
                sys.executable,
                "-m",
                "lilies.watchdog",
                str(os.getpid()),
                str(self.backup_path),
                str(marker),
                str(self.data_directory),
            ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            watchdog = subprocess.Popen(command, creationflags=flags)
        except OSError:
            self._watchdog_restart_failures = min(
                self._watchdog_restart_failures + 1,
                6,
            )
            self._watchdog_next_restart_at = now + min(
                30.0,
                float(2 ** (self._watchdog_restart_failures - 1)),
            )
            raise
        self._watchdog = watchdog
        self._watchdog_marker = marker
        self._watchdog_started_at = now
        return True

    def ensure_recovery_monitor(self) -> None:
        """Start the independent recovery process without changing shell mode."""

        self._ensure_backup()
        self._watchdog_armed = True
        self._start_watchdog()

    def maintain_recovery_monitor(self) -> bool:
        """Best-effort self-heal for a monitor that was already armed.

        Compact mode does not acquire a watchdog merely because the periodic
        timer fires.  Once a full shell or desktop-peek operation has armed
        recovery, however, an unexpectedly exited child is replaced.
        """

        if self.smoke or not self._watchdog_armed or self._watchdog_stopping:
            return False
        was_alive = bool(self._watchdog and self._watchdog.poll() is None)
        is_alive = self._start_watchdog(honor_backoff=True)
        return bool(is_alive and not was_alive)

    def enter_visual(self) -> None:
        self._enter_full("visual")

    def enter_login(self) -> None:
        self._enter_full("login")

    def _enter_full(self, mode: str) -> None:
        self._ensure_backup()
        self._watchdog_armed = True
        self._start_watchdog()
        if not self.smoke:
            _show_desktop_icons(False)
            _show_taskbars(False)
        self.mode = mode
        self.database.set_setting("shell_mode", self.mode)

    def enter_compact(self) -> None:
        self.restore_visual_state()
        self.mode = "compact"
        self.database.set_setting("shell_mode", self.mode)

    def restore_visual_state(self) -> dict[str, object]:
        if self.smoke:
            self._last_restore = {"ok": False, "backupStatus": "diagnostic-skipped",
                                  "errors": ["diagnostic-no-system-changes"]}
        else:
            self._last_restore = restore_from_backup(self.backup_path)
        return self._last_restore

    def reveal_system_drawer(self, seconds: float = 8.0) -> None:
        _show_taskbars(True)

        def hide_later() -> None:
            time.sleep(seconds)
            if self.mode in {"visual", "login"}:
                _show_taskbars(False)

        threading.Thread(target=hide_later, daemon=True).start()

    def maintain_explorer(self) -> bool:
        """Keep Explorer available as the file manager while Lilies is full-screen."""
        if self.smoke or self.mode not in {"visual", "login"} or os.name != "nt":
            return False
        user32 = ctypes.windll.user32
        explorer_alive = bool(
            user32.FindWindowW("Shell_TrayWnd", None)
            or user32.FindWindowW("Progman", None)
            or _find_defview()
        )
        restarted = False
        if not explorer_alive and time.monotonic() - self._last_explorer_restart >= 5:
            self._last_explorer_restart = time.monotonic()
            subprocess.Popen(
                ["explorer.exe"],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            restarted = True
        elif explorer_alive:
            _show_desktop_icons(False)
            _show_taskbars(False)
        return restarted

    def enable_login_shell(self, command: str, confirmation: str) -> None:
        if confirmation != "ENABLE_LILIES_LOGIN_SHELL":
            raise PermissionError("login shell requires explicit confirmation")
        self._ensure_backup()
        if self.mode != "visual":
            raise RuntimeError("login shell can only be enabled after visual mode health check")
        # Keep Explorer as a comma-separated recovery shell.  Lilies hides its
        # desktop chrome after a healthy start; if Lilies cannot launch at all,
        # Winlogon still starts Explorer instead of leaving a blank session.
        safe_command = command if "explorer.exe" in command.casefold() else f"{command}, explorer.exe"
        _write_login_shell(safe_command)
        self.database.set_setting("login_shell_enabled", True)
        self.database.set_setting("login_shell_command", safe_command)

    def disable_login_shell(self) -> None:
        target, _status = _restore_target(self.backup_path)
        original = target["loginShellState"]
        if original is None:
            raise RuntimeError("原登录外壳状态不可验证；未写入猜测的配置")
        if not self.smoke:
            _restore_login_shell_state(original)
            if _read_login_shell_state() != original:
                raise RuntimeError("登录外壳配置尚未恢复")
        self.database.set_setting("login_shell_enabled", False)

    def emergency_restore(self) -> None:
        self.restore_visual_state()
        self.disable_login_shell()

    def health_check(self) -> dict[str, object]:
        user32 = _user32()
        explorer_desktop = bool(user32 and (user32.FindWindowW("Progman", None) or _find_defview()))
        taskbar = bool(user32 and user32.FindWindowW("Shell_TrayWnd", None))
        watchdog = self.smoke or bool(self._watchdog and self._watchdog.poll() is None)
        backup = self.backup_path.is_file()
        writable = os.access(self.data_directory, os.W_OK)
        visual = self.mode == "visual"
        checks = {
            "visualMode": visual,
            "explorerDesktop": explorer_desktop,
            "taskbarRecoverable": taskbar,
            "watchdogRunning": watchdog,
            "backupPresent": backup,
            "dataWritable": writable,
        }
        ok = all(checks.values())
        failed = [name for name, passed in checks.items() if not passed]
        return {
            "ok": ok,
            "message": "视觉模式健康检查通过" if ok else "健康检查未通过：" + "、".join(failed),
            **checks,
        }

    def shutdown(self) -> None:
        self._watchdog_stopping = True
        self._watchdog_armed = False
        result = self.restore_visual_state()
        # An async request or a failed recovery is not a clean exit. Let the
        # independent watchdog perform bounded verification after we exit.
        if self._watchdog_marker and isinstance(result, dict) and result.get("ok") is True:
            try:
                self._watchdog_marker.write_text("clean", "utf-8")
            except OSError:
                pass
        self.hotkey.stop()

    def status(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "backupPath": str(self.backup_path),
            "loginShellEnabled": bool(self.database.get_setting("login_shell_enabled", False)),
            "emergencyHotkey": "Ctrl+Alt+Shift+F12",
            "lastRestore": dict(self._last_restore) if self._last_restore else None,
        }
