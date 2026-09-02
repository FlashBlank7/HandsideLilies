from __future__ import annotations

import ctypes
import os
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
