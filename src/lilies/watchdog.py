from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .core.shell import restore_from_backup


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        result = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
        ctypes.windll.kernel32.CloseHandle(handle)
        return result == 0x00000102
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def watch(pid: int, backup: Path, marker: Path, peek_data_directory: Path | None = None) -> int:
    while _alive(pid):
        time.sleep(1)
    if not marker.exists():
        restore_from_backup(backup)
    else:
        try:
            marker.unlink()
        except OSError:
            pass
    # A journal is present only while Lilies itself minimized windows.  The
    # recovery entry point performs strict HWND identity checks, so it is safe
    # to call on clean and unclean exits and becomes a no-op in the usual case.
    if peek_data_directory is not None:
        try:
            from .core.desktop_peek import recover_desktop_peek

            recover_desktop_peek(peek_data_directory)
        except (OSError, RuntimeError, ValueError):
            # A failed restore remains journaled as recovery-pending and the
            # next application start will retry it.
            pass
    return 0


def main() -> int:
    if len(sys.argv) not in {4, 5}:
        return 2
    peek_data_directory = Path(sys.argv[4]) if len(sys.argv) == 5 else None
    return watch(int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), peek_data_directory)


if __name__ == "__main__":
    raise SystemExit(main())
