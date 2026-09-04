from __future__ import annotations

import threading
import ctypes
import os
from ctypes import wintypes

import pytest

from lilies.root_system_move_guard import RootSystemMoveGuard
from lilies.windows_drag_proxy import _NativeMoveReleaseSentinel


def _guard(*, down: threading.Event, posts: list, cancelled: threading.Event):
    def post(handle):
        posts.append(("cancel", handle))
        cancelled.set()
        return True

    sentinel = _NativeMoveReleaseSentinel(
        left_button_is_down=down.is_set,
        post_cancel=post,
        target_is_valid=lambda handle: handle == 1234,
    )
    return RootSystemMoveGuard(sentinel=sentinel)


def test_root_release_before_sc_move_dispatch_cancels_without_qt_loop():
    down, cancelled = threading.Event(), threading.Event()
    down.set()
    posts = []
    guard = _guard(down=down, posts=posts, cancelled=cancelled)
    try:
        def start():
            posts.append(("sc-move", 1234))
            # No Qt event pumping: release raced ahead of command dispatch.
            down.clear()
            return True

        assert guard.start(1234, 1, start)
        assert cancelled.wait(1.0)
        assert posts == [("sc-move", 1234), ("cancel", 1234)]
        guard.finish(1)
    finally:
        guard.close()


def test_reentrant_root_finish_orders_cancel_after_post_and_before_next_move():
    down, cancelled = threading.Event(), threading.Event()
    down.set()
    posts = []
    guard = _guard(down=down, posts=posts, cancelled=cancelled)
    try:
        def start():
            guard.finish(1)  # ReleaseCapture/QML cancellation on start's stack.
            assert posts == []
            posts.append(("sc-move", 1234))
            return True

        assert guard.start(1234, 1, start)
        assert posts == [("sc-move", 1234), ("cancel", 1234)]
        assert guard.start(1234, 2, lambda: posts.append(("next-move", 1234)) or True)
        guard.finish(1)  # Old completion must not retire the new guard.
        assert posts[-1] == ("next-move", 1234)
        guard.finish(2)
        assert posts[-1] == ("cancel", 1234)
    finally:
        guard.close()


def test_rejected_native_start_has_no_guard_cancel_and_can_retry():
    down, cancelled = threading.Event(), threading.Event()
    down.set()
    posts = []
    guard = _guard(down=down, posts=posts, cancelled=cancelled)
    try:
        assert not guard.start(1234, 1, lambda: False)
        assert guard.last_rejection == "platform-refused"
        assert posts == []
        assert guard.start(1234, 2, lambda: True)
        guard.finish(2)
        assert posts == [("cancel", 1234)]
    finally:
        guard.close()


def test_release_guard_refuses_second_owner_and_unavailable_worker():
    down, cancelled = threading.Event(), threading.Event()
    down.set()
    posts = []
    guard = _guard(down=down, posts=posts, cancelled=cancelled)
    try:
        assert guard.start(1234, 1, lambda: True)
        assert not guard.start(1234, 2, lambda: posts.append("unexpected") or True)
        assert posts == []
        guard.finish(1)
        guard._sentinel.close()
        assert not guard.start(1234, 3, lambda: posts.append("unexpected") or True)
        assert guard.last_rejection == "release-guard-unavailable"
        assert "unexpected" not in posts
    finally:
        guard.close()


def test_close_during_start_defers_cancel_and_closes_worker_after_return():
    down, cancelled = threading.Event(), threading.Event()
    down.set()
    posts = []
    guard = _guard(down=down, posts=posts, cancelled=cancelled)

    def start():
        guard.close()
        assert posts == []
        posts.append(("sc-move", 1234))
        return True

    assert guard.start(1234, 1, start)
    assert posts == [("sc-move", 1234), ("cancel", 1234)]
    assert guard._sentinel.worker_ready is False
    assert guard.start(1234, 2, lambda: True) is False


def test_root_identity_tag_failure_declines_before_native_request():
    down, cancelled = threading.Event(), threading.Event()
    down.set()
    posts = []
    guard = _guard(down=down, posts=posts, cancelled=cancelled)
    guard._bind_target = lambda _handle, _ticket: False
    try:
        assert not guard.start(1234, 1, lambda: posts.append("unexpected") or True)
        assert guard.last_rejection == "root-identity-unavailable"
        assert posts == []
    finally:
        guard.close()


def test_release_cancellation_retries_queue_pressure_without_gui_timer():
    down, cancelled = threading.Event(), threading.Event()
    attempts = []

    def post(handle):
        attempts.append(handle)
        if len(attempts) < 4:
            return False
        cancelled.set()
        return True

    sentinel = _NativeMoveReleaseSentinel(
        left_button_is_down=down.is_set, post_cancel=post,
        target_is_valid=lambda handle: handle == 1234,
    )
    guard = RootSystemMoveGuard(sentinel=sentinel)
    try:
        assert guard.start(1234, 1, lambda: True)
        assert cancelled.wait(1.0)
        assert attempts == [1234] * 4
        guard.finish(1)
    finally:
        guard.close()


@pytest.mark.skipif(os.name != "nt", reason="hidden User32 HWND identity smoke")
def test_windows_hidden_root_tag_marshalling_cleanup_and_reuse_protection():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.GetPropW.restype = wintypes.HANDLE
    user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
    user32.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    # WS_POPUP without WS_VISIBLE. Never call ShowWindow, activation or input.
    hwnd = user32.CreateWindowExW(
        0x08000080, "STATIC", "", 0x80000000,
        0, 0, 1, 1, None, None, None, None,
    )
    assert hwnd
    guard = RootSystemMoveGuard()
    guard._sentinel._left_button_is_down = lambda: True
    calls = []
    try:
        assert not user32.IsWindowVisible(hwnd)
        assert guard.start(hwnd, 1, lambda: calls.append("native") or True)
        ticket = guard._ticket
        assert calls == ["native"]
        assert int(user32.GetPropW(hwnd, guard._owner_property) or 0) == ticket
        assert guard._sentinel._target_is_valid(hwnd)
        guard.finish(1)
        assert not user32.GetPropW(hwnd, guard._owner_property)
        assert guard.start(hwnd, 2, lambda: True)
        user32.RemovePropW(hwnd, guard._owner_property)
        assert not guard._sentinel._target_is_valid(hwnd)
        assert not guard._sentinel._post_cancel(hwnd)
        user32.SetPropW(hwnd, guard._owner_property, wintypes.HANDLE(999999))
        assert not guard._sentinel._target_is_valid(hwnd)
        assert not guard._sentinel._post_cancel(hwnd)
        assert not user32.IsWindowVisible(hwnd)
        guard.finish(2)
    finally:
        guard.close()
        user32.DestroyWindow(hwnd)
