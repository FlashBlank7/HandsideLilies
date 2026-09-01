from __future__ import annotations

"""Ephemeral input-rhythm aggregation without key or text capture.

The only Windows signals read are ``LASTINPUTINFO`` and the current cursor
position.  Raw positions/ticks remain private to this object, rolling events
live in memory only, and the public snapshot contains aggregate counts and an
eight-way direction.  There is intentionally no database/path dependency.
"""

import ctypes
import math
import os
import statistics
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True, slots=True)
class NativeInputSample:
    last_input_tick_ms: int
    uptime_ms: int
    cursor_x: int
    cursor_y: int

    @property
    def idle_seconds(self) -> float:
        # LASTINPUTINFO and GetTickCount use wrapping DWORD ticks.
        elapsed = (int(self.uptime_ms) - int(self.last_input_tick_ms)) & 0xFFFFFFFF
        return elapsed / 1000.0


class InputSampleProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def read(self) -> NativeInputSample | None: ...


class Win32InputSampleProvider:
    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    def __init__(self) -> None:
        self._user32 = None
        self._kernel32 = None

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def read(self) -> NativeInputSample | None:
        if not self.available:
            return None
        try:
            if self._user32 is None:
                self._user32 = ctypes.WinDLL("User32.dll", use_last_error=True)
                self._user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
                self._user32.GetLastInputInfo.restype = wintypes.BOOL
                self._user32.GetCursorPos.argtypes = [ctypes.c_void_p]
                self._user32.GetCursorPos.restype = wintypes.BOOL
            if self._kernel32 is None:
                self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
                self._kernel32.GetTickCount.argtypes = []
                self._kernel32.GetTickCount.restype = wintypes.DWORD
            info = self._LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            point = self._POINT()
            if not self._user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            if not self._user32.GetCursorPos(ctypes.byref(point)):
                return None
            return NativeInputSample(
                int(info.dwTime),
                int(self._kernel32.GetTickCount()),
                int(point.x),
                int(point.y),
            )
        except (AttributeError, OSError):
            return None


@dataclass(frozen=True, slots=True)
class _PulseEvent:
    at: float
    dx: int
    dy: int
    pointer: bool


class InputPulseSource:
    """Sample at 50–100 ms and expose only a short rolling aggregate."""

    def __init__(
        self,
        provider: InputSampleProvider | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        window_seconds: float = 8.0,
        sample_interval_seconds: float = 0.075,
        enabled: bool = True,
    ) -> None:
        self.provider = provider or Win32InputSampleProvider()
        self.clock = clock
        self.window_seconds = max(3.0, min(float(window_seconds), 10.0))
        self.sample_interval_seconds = max(
            0.05, min(float(sample_interval_seconds), 0.1)
        )
        self.enabled = bool(enabled)
        self.suppressed = False
        self._events: deque[_PulseEvent] = deque()
        self._last_raw: NativeInputSample | None = None
        self._idle_seconds = math.inf
        self._sampled_at = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        try:
            return bool(self.provider.available)
        except Exception:
            return False

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if not self.enabled or self.suppressed or not self.available:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="lilies-input-pulse",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def set_enabled(self, value: bool) -> None:
        next_value = bool(value)
        if self.enabled == next_value:
            return
        self.enabled = next_value
        if not next_value:
            self.stop()
            self.clear()

    def set_suppressed(self, value: bool) -> None:
        self.suppressed = bool(value)
        if self.suppressed:
            # BLOCKED/SILENT must erase even the short-lived pulse immediately.
            self.clear()

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_raw = None
            self._idle_seconds = math.inf
            self._sampled_at = 0.0

    def sample(self, now: float | None = None) -> dict[str, object]:
        current = self.clock() if now is None else float(now)
        if not self.enabled or self.suppressed or not self.available:
            return self.snapshot(current)
        try:
            raw = self.provider.read()
        except Exception:
            raw = None
        if raw is None:
            return self.snapshot(current)
        with self._lock:
            previous = self._last_raw
            if previous is not None and raw.last_input_tick_ms != previous.last_input_tick_ms:
                dx = int(raw.cursor_x) - int(previous.cursor_x)
                dy = int(raw.cursor_y) - int(previous.cursor_y)
                self._events.append(_PulseEvent(current, dx, dy, bool(dx or dy)))
            self._last_raw = raw
            self._idle_seconds = max(0.0, raw.idle_seconds)
            self._sampled_at = current
            self._prune(current)
            return self._snapshot_locked(current)

    def snapshot(self, now: float | None = None) -> dict[str, object]:
        current = self.clock() if now is None else float(now)
        with self._lock:
            self._prune(current)
            return self._snapshot_locked(current)

    def _run(self) -> None:
        while not self._stop.is_set() and self.enabled and not self.suppressed:
            self.sample()
            self._stop.wait(self.sample_interval_seconds)

    def _prune(self, current: float) -> None:
        threshold = current - self.window_seconds
        while self._events and self._events[0].at < threshold:
            self._events.popleft()

    def _snapshot_locked(self, current: float) -> dict[str, object]:
        events = tuple(self._events)
        pointer_events = tuple(event for event in events if event.pointer)
        stationary_count = len(events) - len(pointer_events)
        direction, distance = self._direction(pointer_events)
        intervals = [
            events[index].at - events[index - 1].at
            for index in range(1, len(events))
            if events[index].at > events[index - 1].at
        ]
        if len(intervals) >= 2:
            mean = statistics.fmean(intervals)
            burstiness = min(4.0, statistics.pstdev(intervals) / mean) if mean else 0.0
        else:
            burstiness = 0.0
        rate = len(events) / self.window_seconds

        if not self.enabled:
            state = "disabled"
        elif self.suppressed:
            state = "suppressed"
        elif not self.available:
            state = "unavailable"
        elif not math.isfinite(self._idle_seconds) or self._idle_seconds >= 2.0:
            state = "idle"
        elif rate >= 8.0 or burstiness >= 1.2:
            state = "burst"
        else:
            state = "active"
        return {
            "enabled": self.enabled,
            "suppressed": self.suppressed,
            "available": self.available,
            "state": state,
            "windowSeconds": self.window_seconds,
            "eventCount": len(events),
            "pointerEvents": len(pointer_events),
            "stationaryEvents": stationary_count,
            "activityRate": round(rate, 4),
            "burstiness": round(burstiness, 4),
            "idleSeconds": (
                round(self._idle_seconds, 3)
                if math.isfinite(self._idle_seconds)
                else None
            ),
            "cursorDirection": direction,
            "cursorDistance": round(distance, 2),
            "sampledAt": round(self._sampled_at, 3) if self._sampled_at else None,
        }

    @staticmethod
    def _direction(events: tuple[_PulseEvent, ...]) -> tuple[str, float]:
        if not events:
            return "none", 0.0
        dx = sum(event.dx for event in events)
        dy = sum(event.dy for event in events)
        distance = sum(math.hypot(event.dx, event.dy) for event in events)
        if dx == 0 and dy == 0:
            return "none", distance
        # Screen Y grows downwards.  Starting at east and walking clockwise
        # produces stable, coarse gaze hints without exposing coordinates.
        labels = (
            "east",
            "south-east",
            "south",
            "south-west",
            "west",
            "north-west",
            "north",
            "north-east",
        )
        angle = math.atan2(dy, dx)
        index = int(round(angle / (math.pi / 4))) % 8
        return labels[index], distance


__all__ = [
    "InputPulseSource",
    "InputSampleProvider",
    "NativeInputSample",
    "Win32InputSampleProvider",
]
