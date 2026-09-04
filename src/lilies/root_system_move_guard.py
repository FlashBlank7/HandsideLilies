from __future__ import annotations

"""Generation-fenced button-release guard for Lilies' own native window.

QWindow posts its system-move command. A button release can precede dispatch,
so a GUI timer alone cannot guarantee that User32 will leave its modal loop.
This adapter reuses the proxy's tested off-GUI sentinel; it never injects
input, changes focus, or takes a screenshot.
"""

import ctypes
import os
import uuid
from ctypes import wintypes
from typing import Callable

from .windows_drag_proxy import _NativeMoveReleaseSentinel


class RootSystemMoveGuard:
    def __init__(self, *, sentinel=None) -> None:
        self._bind_target = lambda _handle, _ticket: True
        self._unbind_target = lambda _handle, _ticket: None
        self._pending_unbind: list[tuple[int, int]] = []
        self._sentinel = sentinel or self._create_windows_sentinel()
        self._sentinel.ensure_worker()  # Startup only, never per pointer event.
        self._handle = 0
        self._ticket = 0
        self._session = 0
        self._starting = False
        self._retire_after_start = False
        self._closed = False
        self.last_rejection = ""

    def _create_windows_sentinel(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
        user32.SetPropW.restype = wintypes.BOOL
        user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.GetPropW.restype = wintypes.HANDLE
        user32.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.RemovePropW.restype = wintypes.HANDLE
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        owner_thread = int(kernel32.GetCurrentThreadId())
        owner_pid = os.getpid()
        owner_property = "Lilies.RootMove." + uuid.uuid4().hex
        self._owner_property = owner_property
        expected_owner = [0, 0]

        def bind(handle: int, ticket: int) -> bool:
            if not user32.SetPropW(handle, owner_property, wintypes.HANDLE(ticket)):
                return False
            expected_owner[:] = [handle, ticket]
            return True

        self._bind_target = bind

        def unbind(handle: int, ticket: int) -> None:
            if int(user32.GetPropW(handle, owner_property) or 0) == ticket:
                user32.RemovePropW(handle, owner_property)

        self._unbind_target = unbind

        def valid(handle: int) -> bool:
            pid = wintypes.DWORD()
            thread = int(user32.GetWindowThreadProcessId(handle, ctypes.byref(pid)))
            return bool(
                thread == owner_thread and int(pid.value) == owner_pid
                and handle == expected_owner[0]
                and int(user32.GetPropW(handle, owner_property) or 0)
                == expected_owner[1]
                and expected_owner[1] > 0
            )

        def post(handle: int) -> bool:
            return bool(valid(handle) and user32.PostMessageW(handle, 0x001F, 0, 0))

        def force(handle: int) -> bool:
            if not valid(handle):
                return False
            result = ctypes.c_size_t()
            return bool(user32.SendMessageTimeoutW(
                handle, 0x001F, 0, 0, 0x0002 | 0x0020, 40, ctypes.byref(result)
            ))

        return _NativeMoveReleaseSentinel(
            left_button_is_down=lambda: bool(user32.GetAsyncKeyState(
                0x02 if user32.GetSystemMetrics(23) else 0x01
            ) & 0x8000),
            post_cancel=post,
            target_is_valid=valid,
            force_cancel=force,
        )

    def start(self, handle: int, session: int, callback: Callable[[], bool]) -> bool:
        if self._closed or self._session:
            self.last_rejection = "release-guard-busy"
            return False
        ticket = self._sentinel.reserve(int(handle))
        if ticket <= 0:
            self.last_rejection = "release-guard-unavailable"
            return False
        for old_handle, old_ticket in self._pending_unbind:
            self._unbind_target(old_handle, old_ticket)
        self._pending_unbind.clear()
        if not self._bind_target(int(handle), ticket):
            self._sentinel.abort(int(handle), ticket)
            self.last_rejection = "root-identity-unavailable"
            return False
        self._handle, self._ticket, self._session = int(handle), ticket, int(session)
        self._starting = True
        self._retire_after_start = False
        try:
            started = bool(callback())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            started = False
            self.last_rejection = "platform-error"
        else:
            self.last_rejection = "" if started else "platform-refused"
        self._starting = False
        if not started:
            self._sentinel.abort(self._handle, ticket)
            self._unbind_target(self._handle, ticket)
            self._clear()
            if self._closed:
                self._sentinel.close(0.25)
            return False
        # The queued SC_MOVE precedes every sentinel CANCEL. A synchronous
        # capture-loss completion during callback only sets a pending flag;
        # it may not retire the reservation before that command is posted.
        if not self._sentinel.commit(self._handle, ticket):
            # A successful QWindow request remains an owned native operation,
            # even if guard arming reports a terminal cancellation. Never
            # start the proxy/direct owner over that queued request.
            self._sentinel.cancel(self._handle, ticket)
        if self._retire_after_start:
            self.finish(session)
        if self._closed:
            self._sentinel.close(0.25)
        return True

    def _clear(self) -> None:
        self._handle = self._ticket = self._session = 0
        self._retire_after_start = False

    def finish(self, session: int) -> None:
        if int(session) != self._session or not self._session:
            return
        if self._starting:
            self._retire_after_start = True
            return
        self._sentinel.cancel(self._handle, self._ticket)
        cancel_pending = self._sentinel.state_for(self._handle, self._ticket) in {
            self._sentinel._CANCEL_POSTING, self._sentinel._POISONED
        }
        self._sentinel.retire(self._handle, self._ticket)
        if cancel_pending:
            # Keep HWND identity proof until the asynchronous CANCEL is in
            # the queue. A later successful reserve proves that fence passed.
            self._pending_unbind.append((self._handle, self._ticket))
        else:
            self._unbind_target(self._handle, self._ticket)
        self._clear()

    def close(self) -> None:
        self._closed = True
        if self._starting:
            self._retire_after_start = True
            return
        if self._session:
            self.finish(self._session)
        self._sentinel.close(0.25)
        for handle, ticket in self._pending_unbind:
            self._unbind_target(handle, ticket)
        self._pending_unbind.clear()
