from __future__ import annotations

"""Event-driven, taskbar-like window catalogue.

The service keeps native enumeration behind a small provider protocol so the
grouping/MRU logic is deterministic in tests and harmless on non-Windows
systems.  Every flattened window retains the legacy ``handle``/``title``
shape while adding the fields needed by the v0.3 Dock and pet habitat.
"""

import ctypes
import math
import ntpath
import os
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, Sequence

from . import windows
from .win_event import WinEvent, WinEventHub, WinEventKind


DWMWA_EXTENDED_FRAME_BOUNDS = 9
MONITOR_DEFAULTTONEAREST = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_INSUFFICIENT_BUFFER = 122
CLSCTX_ALL = 23
COINIT_APARTMENTTHREADED = 0x2


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "_GUID":
        raw = uuid.UUID(value).bytes_le
        return cls(
            int.from_bytes(raw[0:4], "little"),
            int.from_bytes(raw[4:6], "little"),
            int.from_bytes(raw[6:8], "little"),
            (ctypes.c_ubyte * 8).from_buffer_copy(raw[8:16]),
        )


class _VirtualDesktopQuery:
    """Narrow wrapper over the documented IVirtualDesktopManager interface."""

    CLSID = _GUID.parse("AA509086-5CA9-4C25-8F95-589D3C07B48A")
    IID = _GUID.parse("A5CD92FF-29BE-454C-8D04-D82879FB3F1B")

    def __init__(self) -> None:
        self._pointer = ctypes.c_void_p()
        self._ole32 = None
        self._uninitialize = False
        if os.name != "nt":
            return
        try:
            self._ole32 = ctypes.OleDLL("ole32")
            initialized = int(self._ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
            self._uninitialize = initialized in (0, 1)
            result = int(
                self._ole32.CoCreateInstance(
                    ctypes.byref(self.CLSID),
                    None,
                    CLSCTX_ALL,
                    ctypes.byref(self.IID),
                    ctypes.byref(self._pointer),
                )
            )
            if result < 0:
                self._pointer = ctypes.c_void_p()
        except (AttributeError, OSError, TypeError, ValueError):
            self._pointer = ctypes.c_void_p()

    def is_current(self, handle: int) -> bool:
        if not self._pointer.value:
            return True
        try:
            vtable = ctypes.cast(
                self._pointer,
                ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
            ).contents
            callback = ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.c_void_p,
                wintypes.HWND,
                ctypes.POINTER(wintypes.BOOL),
            )(vtable[3])
            current = wintypes.BOOL(False)
            result = int(callback(self._pointer, wintypes.HWND(handle), ctypes.byref(current)))
            return bool(current.value) if result >= 0 else True
        except (OSError, TypeError, ValueError):
            return True

    def close(self) -> None:
        if self._pointer.value:
            try:
                vtable = ctypes.cast(
                    self._pointer,
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
                ).contents
                release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
                release(self._pointer)
            except (OSError, TypeError, ValueError):
                pass
            self._pointer = ctypes.c_void_p()
        if self._uninitialize and self._ole32 is not None:
            self._ole32.CoUninitialize()
            self._uninitialize = False


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


@dataclass(frozen=True, slots=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @classmethod
    def from_value(cls, value: object) -> "WindowRect | None":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            try:
                return cls(
                    int(value["left"]),
                    int(value["top"]),
                    int(value["right"]),
                    int(value["bottom"]),
                )
            except (KeyError, TypeError, ValueError):
                return None
        if isinstance(value, (list, tuple)) and len(value) == 4:
            try:
                return cls(*(int(item) for item in value))
            except (TypeError, ValueError):
                return None
        return None

    def to_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "width": self.width,
            "height": self.height,
        }


def frame_covers_monitor(
    frame_rect: WindowRect | None,
    monitor_rect: WindowRect | None,
    *,
    tolerance: int = 2,
) -> bool:
    """Return whether a native window frame covers its monitor.

    DWM extended-frame bounds normally match a borderless full-screen monitor
    exactly.  The ``GetWindowRect`` fallback can include a very small resize
    border, so a two-physical-pixel edge tolerance keeps that fallback useful
    without mistaking an ordinary maximized window (which leaves the taskbar
    work area uncovered) for full-screen.

    The helper is deliberately pure: monitor/full-screen policy can be tested
    without enumerating or interacting with any real desktop window.
    """

    if frame_rect is None or monitor_rect is None:
        return False
    if frame_rect.width <= 0 or frame_rect.height <= 0:
        return False
    if monitor_rect.width <= 0 or monitor_rect.height <= 0:
        return False
    edge_tolerance = max(0, int(tolerance))
    return bool(
        frame_rect.left <= monitor_rect.left + edge_tolerance
        and frame_rect.top <= monitor_rect.top + edge_tolerance
        and frame_rect.right >= monitor_rect.right - edge_tolerance
        and frame_rect.bottom >= monitor_rect.bottom - edge_tolerance
    )


def canonical_app_id(
    *,
    aumid: str = "",
    executable_path: str = "",
    process_name: str = "",
    window_class: str = "",
    handle: int = 0,
) -> str:
    """Choose the strongest stable identity available for Dock grouping."""

    application_id = str(aumid or "").strip()
    if application_id:
        return f"aumid:{application_id.casefold()}"
    path = str(executable_path or "").strip().strip('"')
    if path:
        # ntpath is intentional: tests and future non-Windows tooling should
        # canonicalise a Windows path exactly like the production process.
        normalized = ntpath.normcase(ntpath.normpath(path))
        return f"exe:{normalized}"
    process = Path(str(process_name or "")).name.casefold()
    if process:
        return f"process:{process}"
    class_name = str(window_class or "").strip().casefold()
    if class_name:
        return f"class:{class_name}"
    return f"window:{max(0, int(handle))}"


@dataclass(frozen=True, slots=True)
class WindowRecord:
    handle: int
    title: str
    app_id: str = ""
    display_name: str = ""
    process_id: int = 0
    process_name: str = ""
    executable_path: str = ""
    aumid: str = ""
    window_class: str = ""
    active: bool = False
    minimized: bool = False
    visible: bool = True
    maximized: bool = False
    full_screen: bool = False
    monitor_id: str = ""
    current_virtual_desktop: bool = True
    dpi: int = 96
    title_bar_height: int | None = None
    rect: WindowRect | None = None
    work_area: WindowRect | None = None
    icon_key: str = ""
    icon_url: str = ""

    def normalized(self) -> "WindowRecord":
        app_id = self.app_id or canonical_app_id(
            aumid=self.aumid,
            executable_path=self.executable_path,
            process_name=self.process_name,
            window_class=self.window_class,
            handle=self.handle,
        )
        display = self.display_name.strip()
        if not display:
            display = Path(self.process_name).stem or self.title or "Application"
        return replace(
            self,
            app_id=app_id,
            display_name=display[:120],
            title=self.title.strip()[:512],
            icon_key=self.icon_key or self.aumid or self.executable_path or app_id,
        )

    def to_dict(self, *, mru_rank: int | None = None) -> dict[str, object]:
        value: dict[str, object] = {
            # Compatibility contract used by the existing Dock/controller.
            "handle": int(self.handle),
            "title": self.title,
            # v0.3 extensions.
            "appId": self.app_id,
            "displayName": self.display_name,
            "processId": int(self.process_id),
            "processName": self.process_name,
            "executablePath": self.executable_path,
            "applicationUserModelId": self.aumid,
            "windowClass": self.window_class,
            "active": bool(self.active),
            "minimized": bool(self.minimized),
            "visible": bool(self.visible),
            "maximized": bool(self.maximized),
            "fullScreen": bool(self.full_screen),
            "monitorId": self.monitor_id,
            "currentVirtualDesktop": bool(self.current_virtual_desktop),
            "dpi": max(1, int(self.dpi)),
            "dpiScale": round(max(1, int(self.dpi)) / 96.0, 4),
            "titleBarHeight": self.title_bar_height,
            "rect": self.rect.to_dict() if self.rect else None,
            "workArea": self.work_area.to_dict() if self.work_area else None,
            "iconKey": self.icon_key,
            "iconUrl": self.icon_url,
        }
        if mru_rank is not None:
            value["mruRank"] = int(mru_rank)
        return value


@dataclass(frozen=True, slots=True)
class WindowGroup:
    app_id: str
    display_name: str
    icon_key: str
    icon_url: str
    windows: tuple[WindowRecord, ...]
    mru_ranks: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        preferred = self.windows[0]
        active = any(item.active for item in self.windows)
        monitor_ids = list(
            dict.fromkeys(item.monitor_id for item in self.windows if item.monitor_id)
        )
        return {
            # Compatibility for callers that still treat an app group as one
            # representative window.
            "handle": preferred.handle,
            "title": preferred.title,
            "appId": self.app_id,
            "displayName": self.display_name,
            "iconKey": self.icon_key,
            "iconUrl": self.icon_url,
            "active": active,
            "minimized": all(item.minimized for item in self.windows),
            "windowCount": len(self.windows),
            "monitorIds": monitor_ids,
            "mru": [item.handle for item in self.windows],
            "windows": [
                item.to_dict(mru_rank=rank)
                for item, rank in zip(self.windows, self.mru_ranks, strict=True)
            ],
        }


class WindowProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def enumerate_windows(self) -> Sequence[WindowRecord]: ...

    def activate(self, handle: int) -> bool: ...


class NativeWindowProvider:
    """Read-only Win32 provider; all methods fail closed off Windows."""

    @property
    def available(self) -> bool:
        return os.name == "nt" and windows.user32 is not None

    def enumerate_windows(self) -> list[WindowRecord]:
        if not self.available:
            return []
        foreground = windows.foreground_window()
        values: list[WindowRecord] = []
        desktop_query = _VirtualDesktopQuery()
        try:
            for handle in windows.enumerate_manageable_window_handles():
                title = windows.window_title(handle).strip()
                if not title or title == "Lilies in the box":
                    continue
                identity = windows.window_identity(handle) or {}
                process_id = int(identity.get("processId") or 0)
                process_name = str(identity.get("processName") or "")
                executable_path, aumid = self._process_application(process_id, handle)
                rect = self._frame_rect(handle)
                title_bar_height = self._title_bar_height(handle, rect)
                monitor_id, monitor_rect, work_area = self._monitor(handle)
                try:
                    minimized = bool(windows.user32.IsIconic(wintypes.HWND(handle)))
                    maximized = bool(windows.user32.IsZoomed(wintypes.HWND(handle)))
                except (AttributeError, OSError):
                    minimized = False
                    maximized = False
                try:
                    dpi = int(windows.user32.GetDpiForWindow(wintypes.HWND(handle)) or 96)
                except (AttributeError, OSError):
                    dpi = 96
                values.append(
                    WindowRecord(
                        handle=int(handle),
                        title=title,
                        process_id=process_id,
                        process_name=process_name,
                        executable_path=executable_path,
                        aumid=aumid,
                        window_class=windows.window_class_name(handle),
                        active=int(handle) == foreground,
                        minimized=minimized,
                        visible=True,
                        maximized=maximized,
                        full_screen=(
                            not minimized and frame_covers_monitor(rect, monitor_rect)
                        ),
                        monitor_id=monitor_id,
                        current_virtual_desktop=desktop_query.is_current(handle),
                        dpi=dpi,
                        title_bar_height=title_bar_height,
                        rect=rect,
                        work_area=work_area,
                    ).normalized()
                )
        finally:
            desktop_query.close()
        return values

    def activate(self, handle: int) -> bool:
        return windows.activate_window(int(handle)) if self.available else False

    @staticmethod
    def _frame_rect(handle: int) -> WindowRect | None:
        if windows.user32 is None:
            return None
        native_rect = _RECT()
        if windows.dwmapi is not None:
            try:
                result = int(
                    windows.dwmapi.DwmGetWindowAttribute(
                        wintypes.HWND(handle),
                        DWMWA_EXTENDED_FRAME_BOUNDS,
                        ctypes.byref(native_rect),
                        ctypes.sizeof(native_rect),
                    )
                )
                if result == 0:
                    return WindowRect(
                        int(native_rect.left),
                        int(native_rect.top),
                        int(native_rect.right),
                        int(native_rect.bottom),
                    )
            except (AttributeError, OSError):
                pass
        try:
            if windows.user32.GetWindowRect(
                wintypes.HWND(handle), ctypes.byref(native_rect)
            ):
                return WindowRect(
                    int(native_rect.left),
                    int(native_rect.top),
                    int(native_rect.right),
                    int(native_rect.bottom),
                )
        except (AttributeError, OSError):
            pass
        return None

    @staticmethod
    def _title_bar_height(handle: int, frame_rect: WindowRect | None) -> int | None:
        """Return the real top non-client inset, including custom zero insets.

        A zero-height result is meaningful for borderless/custom-title-bar
        windows and allows the habitat policy to avoid sitting over controls.
        ``None`` means the Win32 query failed and lets the controller use its
        DPI-aware standard-caption fallback.
        """

        if windows.user32 is None or frame_rect is None:
            return None
        client_rect = _RECT()
        client_origin = _POINT(0, 0)
        try:
            if not windows.user32.GetClientRect(
                wintypes.HWND(handle), ctypes.byref(client_rect)
            ):
                return None
            if not windows.user32.ClientToScreen(
                wintypes.HWND(handle), ctypes.byref(client_origin)
            ):
                return None
            return max(0, int(client_origin.y) - int(frame_rect.top))
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _monitor(
        handle: int,
    ) -> tuple[str, WindowRect | None, WindowRect | None]:
        if windows.user32 is None:
            return "", None, None
        try:
            monitor = windows.user32.MonitorFromWindow(
                wintypes.HWND(handle), MONITOR_DEFAULTTONEAREST
            )
            if not monitor:
                return "", None, None
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if not windows.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return f"monitor:{int(monitor)}", None, None
            return (
                f"monitor:{int(monitor)}",
                WindowRect(
                    int(info.rcMonitor.left),
                    int(info.rcMonitor.top),
                    int(info.rcMonitor.right),
                    int(info.rcMonitor.bottom),
                ),
                WindowRect(
                    int(info.rcWork.left),
                    int(info.rcWork.top),
                    int(info.rcWork.right),
                    int(info.rcWork.bottom),
                ),
            )
        except (AttributeError, OSError, TypeError, ValueError):
            return "", None, None

    @staticmethod
    def _process_application(process_id: int, handle: int) -> tuple[str, str]:
        if os.name != "nt" or process_id <= 0 or windows.kernel32 is None:
            return "", ""
        process = windows.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(process_id)
        )
        if not process:
            return "", ""
        path = ""
        aumid = ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            try:
                if windows.kernel32.QueryFullProcessImageNameW(
                    process, 0, buffer, ctypes.byref(size)
                ):
                    path = buffer.value
            except (AttributeError, OSError):
                pass
            try:
                length = wintypes.UINT(0)
                windows.kernel32.GetApplicationUserModelId(
                    process, ctypes.byref(length), None
                )
                if int(length.value) > 1:
                    app_buffer = ctypes.create_unicode_buffer(int(length.value))
                    result = int(
                        windows.kernel32.GetApplicationUserModelId(
                            process, ctypes.byref(length), app_buffer
                        )
                    )
                    if result == 0:
                        aumid = app_buffer.value
            except (AttributeError, OSError):
                pass
        finally:
            windows.kernel32.CloseHandle(process)
        return path, aumid


CatalogueListener = Callable[[list[dict[str, object]]], None]
IconResolver = Callable[[WindowRecord], str]


class WindowCatalogService:
    """Debounced grouped window state suitable for a QML list model."""

    _DIRTY_EVENTS = {
        WinEventKind.FOREGROUND,
        WinEventKind.MOVE_SIZE_END,
        WinEventKind.MINIMIZE_START,
        WinEventKind.MINIMIZE_END,
        WinEventKind.DESTROY,
        WinEventKind.SHOW,
        WinEventKind.HIDE,
        WinEventKind.LOCATION_CHANGE,
    }

    def __init__(
        self,
        provider: WindowProvider | None = None,
        event_hub: WinEventHub | None = None,
        *,
        debounce_seconds: float = 0.075,
        safety_refresh_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        icon_resolver: IconResolver | None = None,
    ) -> None:
        self.provider = provider or NativeWindowProvider()
        self.event_hub = event_hub or WinEventHub()
        self._owns_event_hub = event_hub is None
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.safety_refresh_seconds = max(1.0, float(safety_refresh_seconds))
        self.clock = clock
        self.icon_resolver = icon_resolver
        self._records: dict[int, WindowRecord] = {}
        self._mru: list[int] = []
        self._groups: list[WindowGroup] = []
        self._dirty_since: float | None = None
        self._last_refresh = -math.inf
        self._last_error = ""
        self._unsubscribe: Callable[[], None] | None = None
        self._listeners: list[CatalogueListener] = []
        self._lock = threading.RLock()

    def start(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self.event_hub.subscribe(self.handle_event)
        self.event_hub.start()
        self.refresh()

    def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._owns_event_hub:
            self.event_hub.stop()

    def subscribe(self, listener: CatalogueListener) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return unsubscribe

    def handle_event(self, event: WinEvent) -> None:
        if event.kind not in self._DIRTY_EVENTS:
            return
        now = self.clock()
        with self._lock:
            if event.kind is WinEventKind.FOREGROUND and event.hwnd:
                self._promote_mru(event.hwnd)
            if self._dirty_since is None:
                self._dirty_since = now

    def pump(self, now: float | None = None) -> bool:
        """Drain native events and run one debounce/safety-refresh tick."""

        self.event_hub.dispatch_pending()
        return self.tick(now)

    def tick(self, now: float | None = None) -> bool:
        current = self.clock() if now is None else float(now)
        with self._lock:
            dirty_ready = (
                self._dirty_since is not None
                and current - self._dirty_since >= self.debounce_seconds
            )
            safety_due = current - self._last_refresh >= self.safety_refresh_seconds
        if dirty_ready or safety_due:
            self.refresh(current)
            return True
        return False

    def refresh(self, now: float | None = None) -> list[dict[str, object]]:
        current = self.clock() if now is None else float(now)
        try:
            discovered = [
                self._with_icon(item.normalized())
                for item in self.provider.enumerate_windows()
                if int(item.handle) > 0 and item.visible and item.title.strip()
            ]
            error = ""
        except Exception as exc:
            # Preserve the last known catalogue during a transient COM/User32
            # failure instead of flashing an empty Dock.
            with self._lock:
                self._last_error = type(exc).__name__
                self._dirty_since = None
                self._last_refresh = current
                return self.groups()

        unique: dict[int, WindowRecord] = {}
        for item in discovered:
            unique[item.handle] = item
        active = next((item.handle for item in unique.values() if item.active), 0)
        with self._lock:
            self._records = unique
            self._mru = [handle for handle in self._mru if handle in unique]
            if active:
                self._promote_mru(active)
            for handle in unique:
                if handle not in self._mru:
                    self._mru.append(handle)
            rank = {handle: index for index, handle in enumerate(self._mru)}
            by_app: dict[str, list[WindowRecord]] = {}
            for item in unique.values():
                by_app.setdefault(item.app_id, []).append(item)
            groups: list[WindowGroup] = []
            for app_id, items in by_app.items():
                items.sort(key=lambda item: (rank.get(item.handle, 10_000), item.handle))
                preferred = items[0]
                groups.append(
                    WindowGroup(
                        app_id,
                        preferred.display_name,
                        preferred.icon_key,
                        preferred.icon_url,
                        tuple(items),
                        tuple(rank.get(item.handle, 10_000) for item in items),
                    )
                )
            groups.sort(
                key=lambda group: (
                    min(group.mru_ranks, default=10_000),
                    group.display_name.casefold(),
                )
            )
            self._groups = groups
            self._dirty_since = None
            self._last_refresh = current
            self._last_error = error
            listeners = tuple(self._listeners)
            exported = [group.to_dict() for group in groups]
        for listener in listeners:
            try:
                listener([dict(group) for group in exported])
            except Exception:
                continue
        return exported

    def groups(self) -> list[dict[str, object]]:
        with self._lock:
            return [group.to_dict() for group in self._groups]

    def list_windows(self) -> list[dict[str, object]]:
        """Return the legacy flat list enriched with v0.3 fields."""

        with self._lock:
            rank = {handle: index for index, handle in enumerate(self._mru)}
            return [
                self._records[handle].to_dict(mru_rank=rank[handle])
                for handle in self._mru
                if handle in self._records
            ]

    def lookup(self, handle: int) -> WindowRecord | None:
        with self._lock:
            return self._records.get(int(handle))

    def activate(self, handle: int) -> bool:
        target = int(handle)
        with self._lock:
            if target not in self._records:
                return False
        activated = bool(self.provider.activate(target))
        if activated:
            with self._lock:
                self._promote_mru(target)
                if self._dirty_since is None:
                    self._dirty_since = self.clock()
        return activated

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "available": bool(self.provider.available),
                "windowCount": len(self._records),
                "groupCount": len(self._groups),
                "dirty": self._dirty_since is not None,
                "lastError": self._last_error,
            }

    def _promote_mru(self, handle: int) -> None:
        target = int(handle)
        self._mru = [item for item in self._mru if item != target]
        self._mru.insert(0, target)

    def _with_icon(self, record: WindowRecord) -> WindowRecord:
        if record.icon_url or self.icon_resolver is None:
            return record
        try:
            icon_url = str(self.icon_resolver(record) or "")
        except (OSError, RuntimeError, ValueError):
            icon_url = ""
        return replace(record, icon_url=icon_url)


__all__ = [
    "NativeWindowProvider",
    "WindowCatalogService",
    "WindowGroup",
    "WindowProvider",
    "WindowRecord",
    "WindowRect",
    "canonical_app_id",
    "frame_covers_monitor",
]
