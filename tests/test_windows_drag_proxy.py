from __future__ import annotations

import ctypes
import os
import threading
from pathlib import Path

import pytest

import lilies.windows_drag_proxy as proxy_module
from lilies.windows_drag_proxy import (
    ArgbPremultipliedBitmap,
    DEFAULT_PROXY_EX_STYLE,
    DEFAULT_PROXY_STYLE,
    DragDelta,
    WindowRect,
    WindowsDragProxy,
    WindowsDragProxyError,
    WindowsDragProxyUnavailable,
    WS_EX_LAYERED,
    WS_EX_NOACTIVATE,
    WS_EX_TOPMOST,
    WS_EX_TOOLWINDOW,
)


class FakeWin32Api:
    def __init__(self) -> None:
        self.handle = 73
        self.rect_value = WindowRect(0, 0, 0, 0)
        self.calls: list[tuple[object, ...]] = []
        self.move_requested = True

    def create_window(self, *, ex_style: int, style: int) -> int:
        self.calls.append(("create", ex_style, style))
        return self.handle

    def update_layered_window(
        self,
        handle: int,
        *,
        bitmap: ArgbPremultipliedBitmap,
        x: int,
        y: int,
    ) -> bool:
        self.calls.append(("update", handle, bitmap, x, y))
        self.rect_value = WindowRect.from_position(x, y, bitmap.width, bitmap.height)
        return True

    def show_no_activate(
        self,
        handle: int,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> bool:
        self.calls.append(("show-no-activate", handle, x, y, width, height))
        self.rect_value = WindowRect.from_position(x, y, width, height)
        return True

    def request_move(
        self,
        handle: int,
        *,
        cursor_position: tuple[int, int] | None,
    ) -> bool:
        self.calls.append(("request-move", handle, cursor_position))
        return self.move_requested

    def cancel_move(self, handle: int) -> bool:
        self.calls.append(("cancel-move", handle))
        return True

    def get_window_rect(self, handle: int) -> WindowRect:
        self.calls.append(("rect", handle))
        return self.rect_value

    def hide_window(self, handle: int) -> bool:
        self.calls.append(("hide", handle))
        return True

    def destroy_window(self, handle: int) -> bool:
        self.calls.append(("destroy", handle))
        return True


def _bitmap(width: int = 2, height: int = 1) -> ArgbPremultipliedBitmap:
    # Opaque blue followed by a half-alpha premultiplied red pixel.
    row = bytes((255, 0, 0, 255, 0, 0, 128, 128))
    return ArgbPremultipliedBitmap(width, height, row * height)


def test_proxy_uses_layered_tool_noactivate_style_and_fake_lifecycle() -> None:
    api = FakeWin32Api()
    proxy = WindowsDragProxy(api)
    bitmap = _bitmap()

    proxy.upload_bitmap(bitmap)
    shown = proxy.show_at(120, -35)

    assert DEFAULT_PROXY_EX_STYLE == (
        WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
    )
    assert DEFAULT_PROXY_EX_STYLE & 0x00000020 == 0  # WS_EX_TRANSPARENT
    assert api.calls[:3] == [
        ("create", DEFAULT_PROXY_EX_STYLE, DEFAULT_PROXY_STYLE),
        ("update", 73, bitmap, 0, 0),
        ("show-no-activate", 73, 120, -35, 2, 1),
    ]
    assert shown == WindowRect(120, -35, 122, -34)
    assert proxy.is_visible is True

    assert proxy.start_move((130, -30)) is True
    assert proxy.cancel_move() is True
    api.rect_value = WindowRect(151, -13, 153, -12)

    assert proxy.rect() == api.rect_value
    assert proxy.delta() == DragDelta(31, 22)

    final = proxy.finalize()

    assert final.rect == WindowRect(151, -13, 153, -12)
    assert final.delta == (31, 22)
    assert proxy.handle is None
    assert proxy.is_visible is False
    assert [call[0] for call in api.calls[-2:]] == ["hide", "destroy"]


def test_release_sentinel_cancels_posted_move_after_quick_button_up() -> None:
    button_down = threading.Event()
    button_down.set()
    cancelled = threading.Event()
    calls: list[int] = []

    def post_cancel(handle: int) -> bool:
        calls.append(int(handle))
        cancelled.set()
        return True

    sentinel = proxy_module._NativeMoveReleaseSentinel(
        left_button_is_down=button_down.is_set,
        post_cancel=post_cancel,
    )
    sentinel._POLL_SECONDS = 0.001

    ticket = sentinel.arm(73)
    assert ticket > 0
    button_down.clear()

    assert cancelled.wait(0.5) is True
    assert calls == [73]
    # CANCEL_POSTED owns the generation until GUI-side commit/restore retires
    # it, so a same-HWND session cannot start behind an unprocessed cancel.
    assert sentinel.active is True
    assert sentinel.reserve(73) == 0
    assert sentinel.retire(73, ticket) is True
    assert sentinel.active is False
    assert sentinel.close() is True


def test_release_sentinel_retired_generation_never_cancels_new_session() -> None:
    button_down = threading.Event()
    button_down.set()
    cancelled = threading.Event()
    calls: list[int] = []

    def post_cancel(handle: int) -> bool:
        calls.append(int(handle))
        cancelled.set()
        return True

    sentinel = proxy_module._NativeMoveReleaseSentinel(
        left_button_is_down=button_down.is_set,
        post_cancel=post_cancel,
    )
    sentinel._POLL_SECONDS = 0.001

    first = sentinel.arm(73)
    assert first > 0
    assert sentinel.retire(73, first) is True
    second = sentinel.arm(73)
    assert second > first
    # A delayed terminal call from generation 1 cannot touch generation 2,
    # even though the production proxy deliberately reuses this exact HWND.
    assert sentinel.cancel(73, first) is False
    assert sentinel.retire(73, first) is False
    button_down.clear()

    assert cancelled.wait(0.5) is True
    assert calls == [73]
    assert sentinel.retire(73, second) is True
    assert sentinel.active is False
    assert sentinel.close() is True


def test_explicit_sentinel_cancel_retires_worker_without_duplicate_post() -> None:
    button_down = threading.Event()
    button_down.set()
    calls: list[int] = []

    def post_cancel(handle: int) -> bool:
        calls.append(int(handle))
        return True

    sentinel = proxy_module._NativeMoveReleaseSentinel(
        left_button_is_down=button_down.is_set,
        post_cancel=post_cancel,
    )
    sentinel._POLL_SECONDS = 0.001

    ticket = sentinel.arm(73)
    assert ticket > 0
    assert sentinel.cancel(73, ticket) is True
    button_down.clear()
    threading.Event().wait(0.03)

    assert calls == [73]
    assert sentinel.retire(73, ticket) is True
    assert sentinel.active is False
    assert sentinel.close() is True


def test_release_sentinel_must_be_prewarmed_before_reservation() -> None:
    sentinel = proxy_module._NativeMoveReleaseSentinel(
        left_button_is_down=lambda: True,
        post_cancel=lambda _handle: True,
    )

    assert sentinel.reserve(73) == 0
    assert sentinel.ensure_worker() is True
    assert sentinel.worker_ready is True
    ticket = sentinel.reserve(73)
    assert ticket > 0
    assert sentinel.abort(73, ticket) is True
    assert sentinel.close() is True
    assert sentinel.worker_ready is False


def test_poisoned_sentinel_requires_exact_retire_before_next_generation() -> None:
    sentinel = proxy_module._NativeMoveReleaseSentinel(
        left_button_is_down=lambda: True,
        post_cancel=lambda _handle: False,
    )
    assert sentinel.ensure_worker() is True
    ticket = sentinel.reserve(73)
    assert ticket > 0
    # Model the only poison case that remains: the prewarmed worker vanished
    # between reserve/Post DOWN and commit, and the synchronous fallback post
    # also failed. Exact GUI terminal identity must still be able to recover.
    with sentinel._condition:
        sentinel._state = sentinel._POISONED
    assert sentinel.poisoned is True
    assert sentinel.retire(73, ticket + 1) is False
    assert sentinel.retire(74, ticket) is False
    assert sentinel.retire() is False
    assert sentinel.retire(73, ticket) is True
    next_ticket = sentinel.reserve(73)
    assert next_ticket > ticket
    assert sentinel.abort(73, next_ticket) is True
    assert sentinel.close() is True


def test_release_sentinel_retries_transient_post_failures_until_cancelled() -> None:
    attempts = 0
    cancelled = threading.Event()

    def post_cancel(_handle: int) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts < 5:
            return False
        cancelled.set()
        return True

    sentinel = proxy_module._NativeMoveReleaseSentinel(
        left_button_is_down=lambda: False,
        post_cancel=post_cancel,
        target_is_valid=lambda _handle: True,
    )
    sentinel._POLL_SECONDS = 0.001
    sentinel._CANCEL_RETRY_INITIAL_SECONDS = 0.001
    sentinel._CANCEL_RETRY_MAX_SECONDS = 0.003

    ticket = sentinel.arm(73)
    assert ticket > 0
    assert cancelled.wait(0.5) is True
    assert attempts == 5
    assert sentinel.state_for(73, ticket) == sentinel._CANCEL_POSTED
    assert sentinel.poisoned is False
    assert sentinel.retire(73, ticket) is True
    assert sentinel.close() is True


def test_release_sentinel_uses_bounded_force_cancel_after_queue_pressure() -> None:
    forced = threading.Event()
    force_calls: list[int] = []

    def force_cancel(handle: int) -> bool:
        force_calls.append(int(handle))
        forced.set()
        return True

    sentinel = proxy_module._NativeMoveReleaseSentinel(
        left_button_is_down=lambda: False,
        post_cancel=lambda _handle: False,
        target_is_valid=lambda _handle: True,
        force_cancel=force_cancel,
    )
    sentinel._POLL_SECONDS = 0.001
    sentinel._CANCEL_RETRY_INITIAL_SECONDS = 0.001
    sentinel._CANCEL_RETRY_MAX_SECONDS = 0.002
    sentinel._FORCE_CANCEL_AFTER_SECONDS = 0.005

    ticket = sentinel.arm(73)
    assert ticket > 0
    assert forced.wait(0.5) is True
    assert force_calls == [73]
    assert sentinel.state_for(73, ticket) == sentinel._CANCEL_POSTED
    assert sentinel.retire(73, ticket) is True
    assert sentinel.close() is True


def test_exact_retire_cannot_stop_cancel_posting_before_ordered_post() -> None:
    allow_cancel = threading.Event()
    cancelled = threading.Event()
    calls: list[int] = []

    def post_cancel(handle: int) -> bool:
        calls.append(int(handle))
        if not allow_cancel.is_set():
            return False
        cancelled.set()
        return True

    sentinel = proxy_module._NativeMoveReleaseSentinel(
        left_button_is_down=lambda: True,
        post_cancel=post_cancel,
        target_is_valid=lambda _handle: True,
    )
    sentinel._CANCEL_RETRY_INITIAL_SECONDS = 0.001
    sentinel._CANCEL_RETRY_MAX_SECONDS = 0.002
    ticket = sentinel.arm(73)
    assert ticket > 0

    assert sentinel.cancel(73, ticket) is False
    assert sentinel.state_for(73, ticket) == sentinel._CANCEL_POSTING
    assert sentinel.retire(73, ticket) is True
    assert sentinel.reserve(73) == 0
    allow_cancel.set()

    assert cancelled.wait(0.5) is True
    for _ in range(100):
        next_ticket = sentinel.reserve(73)
        if next_ticket > 0:
            break
        threading.Event().wait(0.002)
    assert next_ticket > ticket
    assert len(calls) >= 3
    assert sentinel.abort(73, next_ticket) is True
    assert sentinel.close() is True


def test_native_request_reserves_before_down_and_arms_before_release_capture() -> None:
    events: list[tuple[object, ...]] = []
    large_handle = 2**48 + 73

    class User32:
        def PostMessageW(self, handle, message, wparam, lparam):
            events.append(("post", int(handle), int(message), int(wparam), int(lparam)))
            return True

        def ReleaseCapture(self):
            events.append(("release-capture",))
            return True

    class Sentinel:
        def reserve(self, handle):
            events.append(("reserve", int(handle)))
            return 901

        def commit(self, handle, generation):
            events.append(("commit", int(handle), int(generation)))
            return True

        def abort(self, handle, generation):
            events.append(("abort", int(handle), int(generation)))
            return True

    api = object.__new__(proxy_module.NativeWin32DragProxyApi)
    api._user32 = User32()
    api._owner_thread = None
    api._move_release_sentinel = Sentinel()
    api._move_tickets = {}

    result = api.request_move(large_handle, cursor_position=(-120, 340))

    assert result == 901
    assert [event[0] for event in events] == [
        "reserve",
        "post",
        "commit",
        "release-capture",
    ]
    assert events[0] == ("reserve", large_handle)
    assert events[1][1] == large_handle
    assert api._move_tickets[large_handle] == 901


def test_native_request_never_posts_when_prewarmed_worker_is_unavailable() -> None:
    events: list[str] = []

    class User32:
        def PostMessageW(self, *_args):
            events.append("post")
            return True

        def ReleaseCapture(self):
            events.append("release-capture")
            return True

    class Sentinel:
        def reserve(self, _handle):
            events.append("reserve-refused")
            return 0

    api = object.__new__(proxy_module.NativeWin32DragProxyApi)
    api._user32 = User32()
    api._owner_thread = None
    api._move_release_sentinel = Sentinel()
    api._move_tickets = {}

    assert api.request_move(73, cursor_position=(1, 2)) is False
    assert events == ["reserve-refused"]


def test_native_request_accepts_only_confirmed_cancel_posted_commit_failure() -> None:
    events: list[tuple[object, ...]] = []

    class User32:
        def PostMessageW(self, handle, message, wparam, lparam):
            events.append(("post", int(message)))
            return True

        def ReleaseCapture(self):
            events.append(("release-capture",))
            return True

    class Sentinel:
        _CANCEL_POSTED = "cancel-posted"

        def reserve(self, _handle):
            return 902

        def commit(self, _handle, _generation):
            events.append(("commit-failed",))
            return False

        def state_for(self, _handle, _generation):
            return self._CANCEL_POSTED

    api = object.__new__(proxy_module.NativeWin32DragProxyApi)
    api._user32 = User32()
    api._owner_thread = None
    api._move_release_sentinel = Sentinel()
    api._move_tickets = {}

    result = api.request_move(73, cursor_position=(10, 20))

    assert result == 902
    assert events == [
        ("post", proxy_module.WM_NCLBUTTONDOWN),
        ("commit-failed",),
        ("release-capture",),
    ]
    assert api._move_tickets == {73: 902}


def test_native_request_retracts_poisoned_down_without_releasing_capture() -> None:
    events: list[tuple[object, ...]] = []

    class User32:
        def PostMessageW(self, _handle, message, _wparam, _lparam):
            events.append(("post", int(message)))
            return True

        def PeekMessageW(self, _message, handle, first, last, flags):
            events.append(
                (
                    "retract",
                    int(handle),
                    int(first),
                    int(last),
                    int(flags),
                )
            )
            return True

        def ReleaseCapture(self):
            events.append(("release-capture",))
            return True

        def DestroyWindow(self, _handle):
            events.append(("destroy",))
            return True

    class Sentinel:
        _CANCEL_POSTED = "cancel-posted"

        def reserve(self, _handle):
            return 903

        def commit(self, _handle, _generation):
            return False

        def state_for(self, _handle, _generation):
            return "poisoned"

        def discard_failed_post(self, handle, generation):
            events.append(("discard", int(handle), int(generation)))
            return True

    api = object.__new__(proxy_module.NativeWin32DragProxyApi)
    api._user32 = User32()
    api._owner_thread = None
    api._move_release_sentinel = Sentinel()
    api._move_tickets = {}
    api._handles = {73}
    api._invalidated_handles = set()

    result = api.request_move(73, cursor_position=(10, 20))

    assert result is False
    assert events == [
        ("post", proxy_module.WM_NCLBUTTONDOWN),
        (
            "retract",
            73,
            proxy_module.WM_NCLBUTTONDOWN,
            proxy_module.WM_NCLBUTTONDOWN,
            proxy_module.PM_REMOVE,
        ),
        ("discard", 73, 903),
    ]
    assert api._move_tickets == {}


def test_native_request_destroys_unretractable_poisoned_target_for_rebuild() -> None:
    events: list[str] = []

    class User32:
        def PostMessageW(self, _handle, _message, _wparam, _lparam):
            return True

        def PeekMessageW(self, *_args):
            events.append("retract-failed")
            return False

        def DestroyWindow(self, _handle):
            events.append("destroy")
            return True

        def UnregisterClassW(self, class_name, instance):
            events.append(f"unregister:{class_name}:{int(instance)}")
            return True

        def ReleaseCapture(self):
            events.append("release-capture")
            return True

    class Sentinel:
        _CANCEL_POSTED = "cancel-posted"

        def reserve(self, _handle):
            return 904

        def commit(self, _handle, _generation):
            return False

        def state_for(self, _handle, _generation):
            return "poisoned"

        def discard_failed_post(self, _handle, _generation):
            events.append("discard")
            return True

    api = object.__new__(proxy_module.NativeWin32DragProxyApi)
    api._user32 = User32()
    api._owner_thread = None
    api._move_release_sentinel = Sentinel()
    api._move_tickets = {}
    api._handles = {73}
    api._invalidated_handles = set()
    api._registered = True
    api._class_name = "LiliesPoisonTest"
    api._instance = 17
    api._window_proc = object()

    assert api.request_move(73, cursor_position=(10, 20)) is False
    assert events == [
        "retract-failed",
        "destroy",
        "unregister:LiliesPoisonTest:17",
        "discard",
    ]
    assert api.consume_invalidated_handle(73) is True
    assert api.consume_invalidated_handle(73) is False
    assert 73 not in api._handles
    assert api._registered is False
    assert api._class_name is None


def test_bitmap_update_uses_current_proxy_position_and_can_be_reused_hidden() -> None:
    api = FakeWin32Api()
    proxy = WindowsDragProxy(api)
    first = _bitmap()
    second = ArgbPremultipliedBitmap(1, 2, bytes((0, 0, 0, 0)) * 2)
    proxy.upload_bitmap(first)
    proxy.show_at(10, 20)
    api.rect_value = WindowRect(44, 55, 46, 56)

    proxy.update_bitmap(second)
    final = proxy.finalize(destroy=False)

    update = [call for call in api.calls if call[0] == "update"][-1]
    assert update[2:] == (second, 44, 55)
    assert final.rect == WindowRect(44, 55, 45, 57)
    assert proxy.handle == 73
    assert proxy.is_visible is False
    assert proxy.destroy() is True


def test_proxy_close_shuts_api_even_after_native_handle_is_gone() -> None:
    class ClosableApi(FakeWin32Api):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def close(self, timeout: float = 0.25) -> bool:
            self.calls.append(("close", float(timeout)))
            self.closed = True
            return True

    api = ClosableApi()
    proxy = WindowsDragProxy(api)
    proxy.upload_bitmap(_bitmap())
    assert proxy.destroy() is True

    assert proxy.close() is True
    assert api.closed is True
    assert api.calls[-1] == ("close", 0.25)


def test_rgba_conversion_premultiplies_and_padding_is_removed() -> None:
    converted = ArgbPremultipliedBitmap.from_rgba(
        2,
        1,
        bytes((200, 100, 50, 128, 9, 8, 7, 0, 1, 2, 3, 4)),
        stride=12,
    )

    assert converted.pixels == bytes((25, 50, 100, 128, 0, 0, 0, 0))
    assert converted.stride == 8

    padded = ArgbPremultipliedBitmap(
        1,
        2,
        bytes((1, 2, 3, 4, 99, 99, 1, 1, 1, 1, 88, 88)),
        stride=6,
    )
    assert padded.tight_pixels == bytes((1, 2, 3, 4, 1, 1, 1, 1))


def test_trusted_qt_premultiplied_path_skips_only_channel_validation() -> None:
    # The public constructor rejects this straight-alpha-looking pixel. Qt's
    # trusted adapter intentionally skips the O(width*height) Python scan, but
    # it still owns a copied byte buffer and validates dimensions/stride.
    with pytest.raises(ValueError, match="premultiplied"):
        ArgbPremultipliedBitmap(1, 1, bytes((9, 0, 0, 8)))

    trusted = ArgbPremultipliedBitmap.from_qt_premultiplied(
        1,
        1,
        bytes((9, 0, 0, 8)),
    )

    assert trusted.pixels == bytes((9, 0, 0, 8))
    assert trusted.tight_pixels == trusted.pixels
    with pytest.raises(ValueError, match="byte length"):
        ArgbPremultipliedBitmap.from_qt_premultiplied(1, 1, b"\x00" * 3)


@pytest.mark.parametrize(
    ("width", "height", "pixels", "stride", "message"),
    [
        (0, 1, b"", None, "positive"),
        (1, 1, b"\x00" * 3, None, "byte length"),
        (1, 1, b"\x00" * 4, 3, "stride"),
        (1, 1, bytes((9, 0, 0, 8)), None, "premultiplied"),
    ],
)
def test_bitmap_rejects_invalid_raw_buffers(
    width: int,
    height: int,
    pixels: bytes,
    stride: int | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ArgbPremultipliedBitmap(width, height, pixels, stride=stride)


def test_failed_move_request_leaves_proxy_available_for_safe_finalize() -> None:
    api = FakeWin32Api()
    api.move_requested = False
    proxy = WindowsDragProxy(api)
    proxy.upload_bitmap(_bitmap())
    proxy.show_at(5, 8)

    assert proxy.request_move() is False
    api.rect_value = WindowRect(9, 11, 11, 12)
    assert proxy.finalize().delta == (4, 3)


def test_injected_adapter_is_required_for_unit_tests_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_module, "WINDOWS_AVAILABLE", False)

    with pytest.raises(WindowsDragProxyUnavailable, match="only on Windows"):
        proxy_module.NativeWin32DragProxyApi()

    # A fake remains usable on every platform and never creates a real window.
    proxy = WindowsDragProxy(FakeWin32Api())
    proxy.upload_bitmap(_bitmap())
    assert proxy.handle == 73
    proxy.destroy()


def test_module_contains_no_desktop_pet_or_application_integration() -> None:
    source = Path(proxy_module.__file__).read_text("utf-8")

    assert "lilies.app" not in source
    assert "PySide6" not in source
    assert "SetForegroundWindow" not in source
    assert "MoveWindow" not in source
    assert "SetWindowLong" not in source
    assert "@_WNDPROC" not in source
    assert "def window_proc(" not in source
    assert "self.def_window_proc" not in source


@pytest.mark.skipif(os.name != "nt", reason="registers one native Win32 class")
def test_native_adapter_uses_def_window_proc_address_without_python_trampoline() -> None:
    api = proxy_module.NativeWin32DragProxyApi()
    handle = api.create_window(
        ex_style=proxy_module.DEFAULT_PROXY_EX_STYLE,
        style=proxy_module.DEFAULT_PROXY_STYLE,
    )
    try:
        stored_address = int(
            ctypes.cast(api._window_proc, ctypes.c_void_p).value or 0
        )
        native_address = int(
            ctypes.cast(api._user32.DefWindowProcW, ctypes.c_void_p).value or 0
        )
        assert stored_address > 0
        assert stored_address == native_address
    finally:
        api.destroy_window(handle)


def test_show_requires_pixels_and_start_move_requires_a_visible_proxy() -> None:
    proxy = WindowsDragProxy(FakeWin32Api())

    with pytest.raises(WindowsDragProxyError, match="upload a bitmap"):
        proxy.show_at(0, 0)
    with pytest.raises(WindowsDragProxyError, match="show the proxy"):
        proxy.start_move()


@pytest.mark.skipif(os.name != "nt", reason="creates one hidden Win32 HWND")
def test_native_adapter_uploads_to_hidden_layered_window_without_focus_change() -> None:
    foreground_before = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    proxy = WindowsDragProxy()
    try:
        proxy.upload_bitmap(
            ArgbPremultipliedBitmap(
                2,
                2,
                bytes((0, 0, 0, 0)) * 4,
            )
        )
        assert int(proxy.handle or 0) > 0
        assert proxy.is_visible is False
        assert int(ctypes.windll.user32.GetForegroundWindow() or 0) == foreground_before
    finally:
        proxy.destroy()
    assert int(ctypes.windll.user32.GetForegroundWindow() or 0) == foreground_before
