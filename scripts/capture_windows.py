from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6.QtGui import QGuiApplication


def windows_for_process(process_id: int) -> list[tuple[int, tuple[int, int, int, int], str]]:
    user32 = ctypes.windll.user32
    values: list[tuple[int, tuple[int, int, int, int], str]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(handle: int, _parameter: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == process_id and user32.IsWindowVisible(handle):
            rect = wintypes.RECT()
            user32.GetWindowRect(handle, ctypes.byref(rect))
            length = user32.GetWindowTextLengthW(handle)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, title, length + 1)
            values.append((int(handle), (rect.left, rect.top, rect.right, rect.bottom), title.value))
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("process_id", type=int)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    app = QGuiApplication([])
    args.output.mkdir(parents=True, exist_ok=True)
    screen = app.primaryScreen()
    for index, (handle, rect, title) in enumerate(windows_for_process(args.process_id)):
        path = args.output / f"window-{index}-{handle}.png"
        screen.grabWindow(handle).save(str(path))
        print(handle, rect, repr(title), path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
