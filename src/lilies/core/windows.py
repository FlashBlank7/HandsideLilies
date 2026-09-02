from __future__ import annotations

import ctypes
import hashlib
import os
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit


if os.name == "nt":
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    kernel32 = ctypes.windll.kernel32
    ole32 = ctypes.windll.ole32
else:
    user32 = None
    dwmapi = None
    kernel32 = None
    ole32 = None


GWL_EXSTYLE = -20
GW_OWNER = 4
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_SHOWMINIMIZED = 2
WPF_ASYNCWINDOWPLACEMENT = 0x0004
CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = -2147417850

_SHELL_WINDOW_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "DV2ControlHost",
}


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wintypes.BYTE),
        ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE),
        ("SystemStatusFlag", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", RECT),
        ("rcDevice", RECT),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]

    @classmethod
    def from_text(cls, value: str) -> "GUID":
        raw = uuid.UUID(value).bytes_le
        return cls.from_buffer_copy(raw)


if os.name == "nt":
    # ctypes otherwise assumes a 32-bit integer return value for pointer-sized
    # HWND/HANDLE values, which can truncate handles in a 64-bit process.
    try:
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindow.restype = wintypes.HWND
        user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
        user32.GetWindowPlacement.restype = wintypes.BOOL
        user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
        user32.SetWindowPlacement.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole32.CoInitializeEx.restype = ctypes.c_long
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        ole32.CoUninitialize.argtypes = []
        ole32.CoUninitialize.restype = None
    except (AttributeError, OSError):
        pass


def _point_values(point: POINT) -> list[int]:
    return [int(point.x), int(point.y)]


def _rect_values(rect: RECT) -> list[int]:
    return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]


def placement_to_dict(value: WINDOWPLACEMENT) -> dict[str, Any]:
    """Convert WINDOWPLACEMENT to lossless JSON-safe values."""
    return {
        "flags": int(value.flags),
        "showCmd": int(value.showCmd),
        "minPosition": _point_values(value.ptMinPosition),
        "maxPosition": _point_values(value.ptMaxPosition),
        "normalPosition": _rect_values(value.rcNormalPosition),
        "device": _rect_values(value.rcDevice),
    }


def _int_values(source: Any, count: int, field: str) -> list[int]:
    if not isinstance(source, (list, tuple)) or len(source) != count:
        raise ValueError(f"{field} must contain {count} integers")
    values: list[int] = []
    for item in source:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{field} must contain only integers")
        if item < -(2**31) or item > 2**31 - 1:
            raise ValueError(f"{field} contains an out-of-range coordinate")
        values.append(item)
    return values


def placement_from_dict(source: dict[str, Any]) -> WINDOWPLACEMENT:
    """Validate journal data before handing it to user32."""
    if not isinstance(source, dict):
        raise ValueError("window placement must be an object")
    flags = source.get("flags", 0)
    show_cmd = source.get("showCmd", 0)
    if isinstance(flags, bool) or not isinstance(flags, int) or not 0 <= flags <= 0xFFFFFFFF:
        raise ValueError("invalid WINDOWPLACEMENT flags")
    if isinstance(show_cmd, bool) or not isinstance(show_cmd, int) or not 0 <= show_cmd <= 11:
        raise ValueError("invalid WINDOWPLACEMENT showCmd")
    min_position = _int_values(source.get("minPosition"), 2, "minPosition")
    max_position = _int_values(source.get("maxPosition"), 2, "maxPosition")
    normal = _int_values(source.get("normalPosition"), 4, "normalPosition")
    device = _int_values(source.get("device", [0, 0, 0, 0]), 4, "device")
    value = WINDOWPLACEMENT()
    value.length = ctypes.sizeof(WINDOWPLACEMENT)
    value.flags = flags
    value.showCmd = show_cmd
    value.ptMinPosition = POINT(*min_position)
    value.ptMaxPosition = POINT(*max_position)
    value.rcNormalPosition = RECT(*normal)
    value.rcDevice = RECT(*device)
    return value


def get_window_placement(handle: int) -> dict[str, Any] | None:
    if user32 is None or not handle or not user32.IsWindow(handle):
        return None
    value = WINDOWPLACEMENT()
    value.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(wintypes.HWND(handle), ctypes.byref(value)):
        return None
    return placement_to_dict(value)


def set_window_placement(handle: int, placement: dict[str, Any]) -> bool:
    if user32 is None or not handle or not user32.IsWindow(handle):
        return False
    value = placement_from_dict(placement)
    value.flags |= WPF_ASYNCWINDOWPLACEMENT
    return bool(user32.SetWindowPlacement(wintypes.HWND(handle), ctypes.byref(value)))


def minimize_window_from_placement(handle: int, placement: dict[str, Any]) -> bool:
    """Minimize without converting WINDOWPLACEMENT workspace coordinates."""
    value = dict(placement)
    value["showCmd"] = SW_SHOWMINIMIZED
    return set_window_placement(handle, value)


def is_window_minimized(handle: int) -> bool:
    return bool(user32 is not None and handle and user32.IsWindow(handle) and user32.IsIconic(handle))


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0) if user32 is not None else 0


def request_foreground_window(handle: int) -> bool:
    if user32 is None or not handle or not user32.IsWindow(handle):
        return False
    # Windows deliberately restricts foreground activation. The return value is
    # therefore best-effort and callers must never retry by stealing focus.
    return bool(user32.SetForegroundWindow(wintypes.HWND(handle)))


def window_class_name(handle: int) -> str:
    if user32 is None or not handle or not user32.IsWindow(handle):
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    copied = int(user32.GetClassNameW(wintypes.HWND(handle), buffer, len(buffer)))
    return buffer.value[:copied] if copied > 0 else ""


def window_title(handle: int) -> str:
    if user32 is None or not handle or not user32.IsWindow(handle):
        return ""
    length = int(user32.GetWindowTextLengthW(wintypes.HWND(handle)))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(min(length, 2048) + 1)
    user32.GetWindowTextW(wintypes.HWND(handle), buffer, len(buffer))
    return buffer.value


def _process_details(process_id: int) -> tuple[int | None, str, str]:
    if kernel32 is None or process_id <= 0:
        return None, "", ""
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process:
        return None, "", ""
    started: int | None = None
    image_path = ""
    try:
        created, exited, kernel_time, user_time = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if kernel32.GetProcessTimes(
            process,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            started = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        try:
            if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                image_path = buffer.value
        except (AttributeError, OSError):
            pass
    finally:
        kernel32.CloseHandle(process)
    process_name = Path(image_path).name.casefold() if image_path else ""
    executable_hash = (
        hashlib.sha256(image_path.casefold().encode("utf-8", errors="replace")).hexdigest()
        if image_path
        else ""
    )
    return started, process_name, executable_hash


def window_identity(handle: int) -> dict[str, Any] | None:
    """Return a privacy-conscious fingerprint that detects recycled HWNDs."""
    if user32 is None or not handle or not user32.IsWindow(handle):
        return None
    process_id = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(process_id))
    if not process_id.value:
        return None
    started, process_name, executable_hash = _process_details(int(process_id.value))
    title = window_title(handle).strip().casefold()
    return {
        "handle": int(handle),
        "processId": int(process_id.value),
        "processStarted": started,
        "processName": process_name,
        "executableHash": executable_hash,
        "className": window_class_name(handle),
        # Raw titles can contain filenames or private document names. A hash is
        # sufficient as a fallback identity signal without persisting the text.
        "titleHash": hashlib.sha256(title.encode("utf-8", errors="replace")).hexdigest() if title else "",
    }


class _VirtualDesktopProbe:
    """Small ctypes wrapper around the public IVirtualDesktopManager query."""

    CLSID = GUID.from_text("AA509086-5CA9-4C25-8F95-589D3C07B48A")
    IID = GUID.from_text("A5CD92FF-29BE-454C-8D04-D82879FB3F1B")

    def __init__(self) -> None:
        self._manager = ctypes.c_void_p()
        self._uninitialize = False

    def __enter__(self) -> "_VirtualDesktopProbe":
        if ole32 is None:
            return self
        initialized = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
        if initialized >= 0:
            self._uninitialize = True
        elif initialized != RPC_E_CHANGED_MODE:
            return self
        result = int(
            ole32.CoCreateInstance(
                ctypes.byref(self.CLSID),
                None,
                CLSCTX_INPROC_SERVER,
                ctypes.byref(self.IID),
                ctypes.byref(self._manager),
            )
        )
        if result < 0:
            self._manager = ctypes.c_void_p()
        return self

    def __exit__(self, *_args: object) -> None:
        if self._manager.value:
            try:
                vtable = ctypes.cast(
                    self._manager,
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
                ).contents
                release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
                release(self._manager)
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        if self._uninitialize and ole32 is not None:
            ole32.CoUninitialize()

    def is_current(self, handle: int) -> bool:
        if not self._manager.value:
            # Current-desktop membership is part of the product contract.  If
            # the public COM service is unavailable, fail closed instead of
            # unexpectedly minimizing windows on another virtual desktop.
            return False
        try:
            vtable = ctypes.cast(
                self._manager,
                ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
            ).contents
            query = ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.c_void_p,
                wintypes.HWND,
                ctypes.POINTER(wintypes.BOOL),
            )(vtable[3])
            current = wintypes.BOOL(False)
            result = int(query(self._manager, wintypes.HWND(handle), ctypes.byref(current)))
            return result >= 0 and bool(current.value)
        except (AttributeError, OSError, TypeError, ValueError):
            return False


def enumerate_manageable_window_handles(
    exclude_process_ids: set[int] | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[int]:
    """Enumerate task-like windows on the current public virtual desktop.

    ``EnumWindows`` invokes a Python callback for every top-level window.  A
    catalogue refresh normally runs on a worker, but that callback still has
    to acquire the GIL.  The optional cooperative cancellation probe lets a
    pointer-critical pet gesture stop the enumeration at the next callback
    instead of competing with the GUI for the remainder of the desktop.
    Existing callers retain the complete, non-cancellable enumeration.
    """
    if user32 is None:
        return []

    def cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            # A broken cancellation source must not leave an input-critical
            # worker running indefinitely.
            return True

    if cancelled():
        return []
    excluded = set(exclude_process_ids or ())
    excluded.add(os.getpid())
    handles: list[int] = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    with _VirtualDesktopProbe() as virtual_desktop:

        def callback(hwnd: int, _lparam: int) -> bool:
            if cancelled():
                return False
            handle = int(hwnd)
            if not user32.IsWindowVisible(hwnd):
                return True
            class_name = window_class_name(handle)
            if class_name in _SHELL_WINDOW_CLASSES:
                return True
            ex_style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
            if ex_style & WS_EX_TOOLWINDOW and not ex_style & WS_EX_APPWINDOW:
                return True
            if user32.GetWindow(hwnd, GW_OWNER) and not ex_style & WS_EX_APPWINDOW:
                return True
            if dwmapi is not None:
                cloaked = wintypes.DWORD(0)
                try:
                    result = dwmapi.DwmGetWindowAttribute(
                        wintypes.HWND(hwnd),
                        DWMWA_CLOAKED,
                        ctypes.byref(cloaked),
                        ctypes.sizeof(cloaked),
                    )
                    if result == 0 and cloaked.value:
                        return True
                except (AttributeError, OSError):
                    pass
            process_id = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if int(process_id.value) in excluded:
                return True
            if not virtual_desktop.is_current(handle):
                return True
            handles.append(handle)
            return len(handles) < 512

        user32.EnumWindows(EnumProc(callback), 0)
    return handles


def list_windows() -> list[dict[str, Any]]:
    if user32 is None:
        return []
    values: list[dict[str, Any]] = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        # Match the Windows taskbar more closely: owned/tool windows are
        # auxiliary UI, while DWM-cloaked UWP/CoreWindow surfaces are not
        # actually present on the current desktop despite IsWindowVisible.
        ex_style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
        if ex_style & WS_EX_TOOLWINDOW and not ex_style & WS_EX_APPWINDOW:
            return True
        if user32.GetWindow(hwnd, GW_OWNER) and not ex_style & WS_EX_APPWINDOW:
            return True
        if dwmapi is not None:
            cloaked = wintypes.DWORD(0)
            try:
                result = dwmapi.DwmGetWindowAttribute(
                    wintypes.HWND(hwnd),
                    DWMWA_CLOAKED,
                    ctypes.byref(cloaked),
                    ctypes.sizeof(cloaked),
                )
                if result == 0 and cloaked.value:
                    return True
            except (AttributeError, OSError):
                pass
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title or title in {"Program Manager", "Lilies in the box"}:
            return True
        values.append({"handle": int(hwnd), "title": title[:160]})
        return len(values) < 80

    user32.EnumWindows(EnumProc(callback), 0)
    return values


def activate_window(handle: int) -> bool:
    if user32 is None or not user32.IsWindow(handle):
        return False
    user32.ShowWindow(handle, 9)
    return bool(user32.SetForegroundWindow(handle))


def system_status() -> dict[str, Any]:
    """Return a tiny, dependency-free status summary for the custom Dock."""
    if os.name != "nt":
        return {"network": "未知", "online": False, "battery": "未知", "charging": False}
    flags = wintypes.DWORD()
    online = bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0))
    power = SYSTEM_POWER_STATUS()
    power_ok = bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(power)))
    no_battery = not power_ok or power.BatteryFlag == 128 or power.BatteryLifePercent == 255
    battery = "台式机" if no_battery else f"{int(power.BatteryLifePercent)}%"
    return {
        "network": "在线" if online else "离线",
        "online": online,
        "battery": battery,
        "charging": bool(power_ok and power.ACLineStatus == 1 and not no_battery),
    }


def window_fully_occluded(handle: int) -> bool:
    """Conservatively detect a foreground window covering the whole scene."""
    if user32 is None or not handle or not user32.IsWindow(handle):
        return False
    foreground = int(user32.GetForegroundWindow() or 0)
    if not foreground or foreground == int(handle):
        return False
    if not user32.IsWindowVisible(foreground) or user32.IsIconic(foreground):
        return False
    target_rect = RECT()
    cover_rect = RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(target_rect)):
        return False
    if not user32.GetWindowRect(foreground, ctypes.byref(cover_rect)):
        return False
    return (
        cover_rect.left <= target_rect.left
        and cover_rect.top <= target_rect.top
        and cover_rect.right >= target_rect.right
        and cover_rect.bottom >= target_rect.bottom
    )


def open_settings(uri: str) -> None:
    allowed = {
        "ms-settings:",
        "ms-settings:network",
        "ms-settings:sound",
        "ms-settings:notifications",
        "ms-settings:display",
    }
    if uri not in allowed:
        raise PermissionError("unsupported settings URI")
    os.startfile(uri)


def open_web_url(url: str) -> None:
    value = url.strip()
    if len(value) > 2048:
        raise ValueError("URL is too long")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PermissionError("only HTTP and HTTPS web addresses are supported")
    if parsed.username or parsed.password:
        raise PermissionError("web addresses containing credentials are not supported")
    os.startfile(value)


def reveal_in_explorer(path: str) -> None:
    value = Path(path).resolve()
    if value.is_file():
        os.spawnl(os.P_NOWAIT, os.environ.get("WINDIR", r"C:\Windows") + r"\explorer.exe", "explorer.exe", "/select,", str(value))
    else:
        os.startfile(str(value))
