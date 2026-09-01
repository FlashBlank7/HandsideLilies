from __future__ import annotations

"""Shared, queue-based Windows accessibility event source.

The native callback deliberately does no application work.  It only converts
the small Win32 payload into :class:`WinEvent` and appends it to an in-memory
queue.  A Qt timer (or a test) calls :meth:`WinEventHub.dispatch_pending` on its
own thread, which keeps Win32 hook callbacks away from QML and application
state.

Importing this module and calling ``start`` are safe on non-Windows systems.
There is no hidden polling fallback there; callers simply receive no native
events and can still use :meth:`WinEventHub.publish` in tests/adapters.
"""

import ctypes
import os
import threading
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Callable


EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MOVESIZESTART = 0x000A
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_SYSTEM_MINIMIZESTART = 0x0016
EVENT_SYSTEM_MINIMIZEEND = 0x0017
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B

OBJID_WINDOW = 0
CHILDID_SELF = 0
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
PM_REMOVE = 0x0001


class WinEventKind(str, Enum):
    FOREGROUND = "foreground"
    MOVE_SIZE_START = "move-size-start"
    MOVE_SIZE_END = "move-size-end"
    MINIMIZE_START = "minimize-start"
    MINIMIZE_END = "minimize-end"
    DESTROY = "destroy"
    SHOW = "show"
    HIDE = "hide"
    LOCATION_CHANGE = "location-change"


_EVENT_KINDS: dict[int, WinEventKind] = {
    EVENT_SYSTEM_FOREGROUND: WinEventKind.FOREGROUND,
    EVENT_SYSTEM_MOVESIZESTART: WinEventKind.MOVE_SIZE_START,
    EVENT_SYSTEM_MOVESIZEEND: WinEventKind.MOVE_SIZE_END,
    EVENT_SYSTEM_MINIMIZESTART: WinEventKind.MINIMIZE_START,
    EVENT_SYSTEM_MINIMIZEEND: WinEventKind.MINIMIZE_END,
    EVENT_OBJECT_DESTROY: WinEventKind.DESTROY,
    EVENT_OBJECT_SHOW: WinEventKind.SHOW,
    EVENT_OBJECT_HIDE: WinEventKind.HIDE,
    EVENT_OBJECT_LOCATIONCHANGE: WinEventKind.LOCATION_CHANGE,
}


@dataclass(frozen=True, slots=True)
class WinEvent:
    kind: WinEventKind
    hwnd: int
    native_event: int
    object_id: int = OBJID_WINDOW
    child_id: int = CHILDID_SELF
    thread_id: int = 0
    native_time_ms: int = 0

    def to_dict(self) -> dict[str, int | str]:
        return {
            "kind": self.kind.value,
            "handle": self.hwnd,
            "nativeEvent": self.native_event,
            "objectId": self.object_id,
            "childId": self.child_id,
            "threadId": self.thread_id,
            "nativeTimeMs": self.native_time_ms,
        }


EventListener = Callable[[WinEvent], None]


class WinEventHub:
    """Fan out selected WinEvents when the owning thread drains the queue."""

    def __init__(self, *, queue_limit: int = 2048) -> None:
        self._queue: deque[WinEvent] = deque(maxlen=max(32, int(queue_limit)))
        self._queue_lock = threading.Lock()
        self._listeners: list[EventListener] = []
        self._listeners_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hooks: list[int] = []
        self._native_callback: object | None = None

    @property
    def available(self) -> bool:
        return os.name == "nt"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._hooks)

    @property
    def pending_count(self) -> int:
        with self._queue_lock:
            return len(self._queue)

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._listeners_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._listeners_lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return unsubscribe

    def start(self) -> bool:
        """Start native collection and return whether a hook thread exists."""

        if not self.available:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_native,
            name="lilies-win-event-hub",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None

    def publish(self, event: WinEvent) -> bool:
        """Queue an adapter/test event without invoking listeners inline."""

        if not isinstance(event, WinEvent) or not event.hwnd:
            return False
        with self._queue_lock:
            # LOCATIONCHANGE can fire hundreds of times while a window moves.
            # Replacing an adjacent duplicate preserves the final geometry and
            # keeps more important foreground/destroy events in the bounded
            # queue.
            if (
                event.kind is WinEventKind.LOCATION_CHANGE
                and self._queue
                and self._queue[-1].kind is WinEventKind.LOCATION_CHANGE
                and self._queue[-1].hwnd == event.hwnd
            ):
                self._queue[-1] = event
            else:
                self._queue.append(event)
        return True

    def publish_native(
        self,
        native_event: int,
        hwnd: int,
        *,
        object_id: int = OBJID_WINDOW,
        child_id: int = CHILDID_SELF,
        thread_id: int = 0,
        native_time_ms: int = 0,
    ) -> bool:
        kind = _EVENT_KINDS.get(int(native_event))
        if kind is None or not int(hwnd or 0):
            return False
        if kind in {
            WinEventKind.DESTROY,
            WinEventKind.SHOW,
            WinEventKind.HIDE,
            WinEventKind.LOCATION_CHANGE,
        } and (int(object_id) != OBJID_WINDOW or int(child_id) != CHILDID_SELF):
            return False
        return self.publish(
            WinEvent(
                kind,
                int(hwnd),
                int(native_event),
                int(object_id),
                int(child_id),
                int(thread_id),
                int(native_time_ms),
            )
        )

    def drain(self, limit: int = 512) -> list[WinEvent]:
        count = max(0, min(int(limit), 4096))
        values: list[WinEvent] = []
        with self._queue_lock:
            while self._queue and len(values) < count:
                values.append(self._queue.popleft())
        return values

    def dispatch_pending(self, limit: int = 512) -> int:
        """Invoke subscribers on the *calling* thread and return event count."""

        events = self.drain(limit)
        with self._listeners_lock:
            listeners = tuple(self._listeners)
        for event in events:
            for listener in listeners:
                try:
                    listener(event)
                except Exception:
                    # A Dock listener must not prevent habitat/activity state
                    # from observing the same native event.
                    continue
        return len(events)

    def _run_native(self) -> None:
        if os.name != "nt":
            return
        try:
            user32 = ctypes.WinDLL("User32.dll", use_last_error=True)
            callback_factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
            callback_type = callback_factory(
                None,
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HWND,
                wintypes.LONG,
                wintypes.LONG,
                wintypes.DWORD,
                wintypes.DWORD,
            )
            user32.SetWinEventHook.argtypes = [
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HMODULE,
                callback_type,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            user32.SetWinEventHook.restype = wintypes.HANDLE
            user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            user32.UnhookWinEvent.restype = wintypes.BOOL

            def on_event(
                _hook: int,
                native_event: int,
                hwnd: int,
                object_id: int,
                child_id: int,
                thread_id: int,
                native_time_ms: int,
            ) -> None:
                try:
                    self.publish_native(
                        int(native_event),
                        int(hwnd or 0),
                        object_id=int(object_id),
                        child_id=int(child_id),
                        thread_id=int(thread_id),
                        native_time_ms=int(native_time_ms),
                    )
                except Exception:
                    # Never unwind Python through User32.
                    return

            self._native_callback = callback_type(on_event)
            flags = WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
            ranges = (
                (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
                (EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND),
                (EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND),
                (EVENT_OBJECT_DESTROY, EVENT_OBJECT_LOCATIONCHANGE),
            )
            self._hooks = [
                int(
                    user32.SetWinEventHook(
                        first,
                        last,
                        None,
                        self._native_callback,
                        0,
                        0,
                        flags,
                    )
                    or 0
                )
                for first, last in ranges
            ]
            self._hooks = [hook for hook in self._hooks if hook]
            if not self._hooks:
                return

            message = wintypes.MSG()
            while not self._stop.wait(0.05):
                while user32.PeekMessageW(
                    ctypes.byref(message), None, 0, 0, PM_REMOVE
                ):
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
        except (AttributeError, OSError):
            return
        finally:
            if os.name == "nt":
                try:
                    user32 = ctypes.WinDLL("User32.dll", use_last_error=True)
                    for hook in self._hooks:
                        user32.UnhookWinEvent(wintypes.HANDLE(hook))
                except (AttributeError, OSError):
                    pass
            self._hooks = []
            self._native_callback = None


__all__ = [
    "CHILDID_SELF",
    "EVENT_OBJECT_DESTROY",
    "EVENT_OBJECT_HIDE",
    "EVENT_OBJECT_LOCATIONCHANGE",
    "EVENT_OBJECT_SHOW",
    "EVENT_SYSTEM_FOREGROUND",
    "EVENT_SYSTEM_MINIMIZEEND",
    "EVENT_SYSTEM_MINIMIZESTART",
    "EVENT_SYSTEM_MOVESIZEEND",
    "EVENT_SYSTEM_MOVESIZESTART",
    "OBJID_WINDOW",
    "WinEvent",
    "WinEventHub",
    "WinEventKind",
]
