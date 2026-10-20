from __future__ import annotations

import os
import ctypes
import sys
import time
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

from .core.shell import restore_from_backup
from .core.recovery import finish_recovery_report


@lru_cache(maxsize=1)
def _kernel32():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, arguments, result in (
        ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
    ):
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _alive(pid: int) -> bool:
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        raise ValueError("invalid watched process")
    if os.name == "nt":
        SYNCHRONIZE = 0x00100000
        api = _kernel32()
        handle = api.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # A process with this ID no longer exists.
                return False
            raise OSError(error, "process liveness unavailable")
        try:
            result = api.WaitForSingleObject(handle, 0)
            if result == 0x00000102:
                return True
            if result == 0:
                return False
            raise OSError("process wait state unavailable")
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def watch(pid: int, backup: Path, marker: Path, peek_data_directory: Path | None = None) -> int:
    while _alive(pid):
        time.sleep(1)
    try:
        clean = marker.read_text("utf-8") == "clean"
    except (OSError, UnicodeError):
        clean = False
    if clean:
        try:
            marker.unlink()
        except OSError:
            pass
        shell_result = {"ok": True}
    else:
        shell_result = restore_from_backup(backup, wait_seconds=8.0)
    # A journal is present only while Lilies itself minimized windows.  The
    # recovery entry point performs strict HWND identity checks, so it is safe
    # to call on clean and unclean exits and becomes a no-op in the usual case.
    if peek_data_directory is None:
        # Legacy callers did not arm desktop-peek recovery.
        return 0 if isinstance(shell_result, dict) and shell_result.get("ok") is True else 4
    report = finish_recovery_report(shell_result, peek_data_directory)
    return 0 if report["ok"] else 4


def main() -> int:
    if len(sys.argv) not in {4, 5}:
        return 2
    peek_data_directory = Path(sys.argv[4]) if len(sys.argv) == 5 else None
    return watch(int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), peek_data_directory)


if __name__ == "__main__":
    raise SystemExit(main())
