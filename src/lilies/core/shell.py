from __future__ import annotations

import ctypes
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from ctypes import wintypes
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


def _user32():
    return ctypes.windll.user32 if os.name == "nt" else None


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
    while True:
        worker = user32.FindWindowExW(0, worker, "WorkerW", None)
        if not worker:
            break
        view = user32.FindWindowExW(worker, 0, "SHELLDLL_DefView", None)
        if view:
            result = int(view)
            break
    return result


def _window_visible(hwnd: int) -> bool:
    user32 = _user32()
    return bool(hwnd and user32 and user32.IsWindowVisible(hwnd))


def _show_taskbars(show: bool) -> None:
    user32 = _user32()
    if user32 is None:
        return
    command = SW_SHOW if show else SW_HIDE
    primary = user32.FindWindowW("Shell_TrayWnd", None)
    if primary:
        user32.ShowWindow(primary, command)
    current = 0
    while True:
        current = user32.FindWindowExW(0, current, "Shell_SecondaryTrayWnd", None)
        if not current:
            break
        user32.ShowWindow(current, command)


def _show_desktop_icons(show: bool) -> None:
    user32 = _user32()
    view = _find_defview()
    if user32 is not None and view:
        user32.ShowWindow(view, SW_SHOW if show else SW_HIDE)


def _read_login_shell() -> str:
    if os.name != "nt":
        return "explorer.exe"
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINLOGON_KEY) as key:
            return str(winreg.QueryValueEx(key, "Shell")[0])
    except OSError:
        return "explorer.exe"


def _write_login_shell(value: str) -> None:
    if os.name != "nt":
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINLOGON_KEY) as key:
        winreg.SetValueEx(key, "Shell", 0, winreg.REG_SZ, value)


def restore_from_backup(path: Path) -> None:
    try:
        backup = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        backup = {"taskbarVisible": True, "desktopIconsVisible": True, "loginShell": "explorer.exe"}
    _show_taskbars(bool(backup.get("taskbarVisible", True)))
    _show_desktop_icons(bool(backup.get("desktopIconsVisible", True)))
    login_shell = str(backup.get("loginShell", "explorer.exe"))
    if login_shell:
        try:
            _write_login_shell(login_shell)
        except OSError:
            pass
    if os.name == "nt" and not ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None):
        try:
            subprocess.Popen(["explorer.exe"], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        except OSError:
            pass


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
        self.hotkey = EmergencyHotkey(self.emergency_restore)
        if not self.smoke:
            self.hotkey.start()

    def capture(self) -> dict[str, object]:
        user32 = _user32()
        taskbar = int(user32.FindWindowW("Shell_TrayWnd", None)) if user32 else 0
        view = _find_defview()
        return {
            "capturedAt": utc_now(),
            "taskbarVisible": _window_visible(taskbar) if taskbar else True,
            "desktopIconsVisible": _window_visible(view) if view else True,
            "loginShell": _read_login_shell(),
        }

    def _ensure_backup(self) -> None:
        if not self.backup_path.exists():
            self.backup_path.write_text(json.dumps(self.capture(), ensure_ascii=False, indent=2), "utf-8")

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

    def restore_visual_state(self) -> None:
        if self.backup_path.exists():
            restore_from_backup(self.backup_path)
        else:
            _show_taskbars(True)
            _show_desktop_icons(True)

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
        original = "explorer.exe"
        if self.backup_path.exists():
            try:
                original = json.loads(self.backup_path.read_text("utf-8")).get("loginShell", original)
            except (OSError, ValueError):
                pass
        _write_login_shell(str(original))
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
        self.restore_visual_state()
        if self._watchdog_marker:
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
        }
