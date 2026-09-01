from __future__ import annotations

"""Read-only Windows z-order diagnostics for the Lilies desktop surface.

The script deliberately does not capture pixels or change any window state.  It
prints only the native geometry/style data needed to distinguish a hidden QML
desktop from one that is visible but ordered underneath Explorer's wallpaper.
"""

import ctypes
import json
import os
from ctypes import wintypes


GWL_STYLE = -16
GWL_EXSTYLE = -20
GW_OWNER = 4
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_CLOAKED = 14


def main() -> int:
    if os.name != "nt":
        print(json.dumps({"supported": False, "windows": []}))
        return 0

    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
        wintypes.LPARAM,
    ]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    try:
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    except (AttributeError, OSError):
        dwmapi = None

    def process_name_for(pid: int) -> str:
        process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(size)
            ):
                return ""
            return os.path.basename(buffer.value)
        finally:
            kernel32.CloseHandle(process)

    rows: list[dict[str, object]] = []
    global_index = 0
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def collect(hwnd: int, _lparam: int) -> bool:
        nonlocal global_index
        handle = int(hwnd)
        current_global_index = global_index
        global_index += 1
        title_length = int(user32.GetWindowTextLengthW(hwnd))
        title_buffer = ctypes.create_unicode_buffer(max(1, title_length + 1))
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        process_id = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        pid = int(process_id.value)
        process_name = process_name_for(pid)
        class_name = class_buffer.value
        title = title_buffer.value
        visible = bool(user32.IsWindowVisible(hwnd))
        relevant = (
            (process_name.casefold() == "liliesinthebox.exe" and visible)
            or class_name in {
                "Progman",
                "WorkerW",
                "SHELLDLL_DefView",
                "Shell_TrayWnd",
                "Shell_SecondaryTrayWnd",
            }
            or title in {"Lilies in the box", "Program Manager"}
        )
        if not relevant:
            return True
        rect = wintypes.RECT()
        has_rect = bool(user32.GetWindowRect(hwnd, ctypes.byref(rect)))
        ex_style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
        cloaked = wintypes.DWORD(0)
        if dwmapi is not None:
            result = int(
                dwmapi.DwmGetWindowAttribute(
                    hwnd,
                    DWMWA_CLOAKED,
                    ctypes.byref(cloaked),
                    ctypes.sizeof(cloaked),
                )
            )
            if result != 0:
                cloaked.value = 0
        rows.append(
            {
                "zIndex": current_global_index,
                "handle": handle,
                "pid": pid,
                "process": process_name,
                "class": class_name,
                "title": title,
                "visible": visible,
                "iconic": bool(user32.IsIconic(hwnd)),
                "cloaked": bool(cloaked.value),
                "owner": int(user32.GetWindow(hwnd, GW_OWNER) or 0),
                "style": int(user32.GetWindowLongW(hwnd, GWL_STYLE)) & 0xFFFFFFFF,
                "exStyle": ex_style,
                "toolWindow": bool(ex_style & WS_EX_TOOLWINDOW),
                "topmost": bool(ex_style & WS_EX_TOPMOST),
                "noActivate": bool(ex_style & WS_EX_NOACTIVATE),
                "rect": (
                    {
                        "left": int(rect.left),
                        "top": int(rect.top),
                        "right": int(rect.right),
                        "bottom": int(rect.bottom),
                    }
                    if has_rect
                    else None
                ),
            }
        )
        return True

    callback = callback_type(collect)
    user32.EnumWindows(callback, 0)
    print(json.dumps({"supported": True, "windows": rows}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
