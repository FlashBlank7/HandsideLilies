from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QMouseEvent
from PySide6.QtQml import QQmlComponent, QQmlEngine

from lilies.app import CompactPointerEventFilter
from lilies.backend import Backend
from lilies.core import windows
from lilies.core.desktop_peek import DesktopPeekService
from test_desktop_peek import FakeWindowApi, placement


@pytest.mark.skipif(os.name != "nt", reason="Windows ABI")
def test_windows_placement_uses_44_bytes_and_roundtrips_hidden_owned_window():
    assert ctypes.sizeof(windows.WINDOWPLACEMENT) == 44
    native = ctypes.WinDLL("user32", use_last_error=True)
    native.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    native.CreateWindowExW.restype = wintypes.HWND
    native.DestroyWindow.argtypes = [wintypes.HWND]
    native.IsWindowVisible.argtypes = [wintypes.HWND]
    handle = native.CreateWindowExW(0x08000080, "STATIC", "Lilies hidden regression",
        0x00CF0000, -30000, -30000, 100, 100, None, None, None, None)
    assert handle
    try:
        original = windows.get_window_placement(handle)
        assert original is not None
        assert not native.IsWindowVisible(handle)
        assert windows.set_window_placement(handle, {**original, "showCmd": 0})
        after = windows.get_window_placement(handle)
        for key in ("minPosition", "maxPosition", "normalPosition"):
            assert after[key] == original[key]
        assert not native.IsWindowVisible(handle)
    finally:
        native.DestroyWindow(handle)


def test_legacy_placement_journals_remain_readable():
    old = placement(3)
    parsed = windows.placement_from_dict(old)
    current = windows.placement_to_dict(parsed)
    assert current == {key: value for key, value in old.items() if key != "device"}
    assert windows.placement_to_dict(windows.placement_from_dict(current)) == current


def test_failed_peek_is_not_reported_as_return_to_work(tmp_path):
    api = FakeWindowApi()
    api.minimize = lambda *args: False
    service = DesktopPeekService(tmp_path, api)
    status = Mock()
    backend = SimpleNamespace(shell=SimpleNamespace(ensure_recovery_monitor=Mock()),
        desktop_peek=service, _set_status=status, desktopPeekChanged=Mock(),
        database=Mock())
    result = Backend.toggleDesktopPeek(backend)
    assert result["minimized"] == 0
    assert result["failed"] == 2
    assert not result["active"]
    assert not service.journal_path.exists()
    assert "Windows 拒绝" in status.call_args.args[0]
    assert "返回工作" not in status.call_args.args[0]


@pytest.mark.parametrize("result, expected", [
    ({"active": False, "minimized": 0, "failed": 0}, "没有需要收起"),
    ({"active": True, "restored": 2, "failed": 1}, "还有 1 个待重试"),
    ({"active": False, "restored": 2, "failed": 0}, "恢复 2 个窗口"),
])
def test_peek_status_distinguishes_noop_and_pending_restore(result, expected):
    status = Mock()
    backend = SimpleNamespace(shell=SimpleNamespace(ensure_recovery_monitor=Mock()),
        desktop_peek=SimpleNamespace(toggle=lambda: result), _set_status=status,
        desktopPeekChanged=Mock(), database=Mock())
    Backend.toggleDesktopPeek(backend)
    assert expected in status.call_args.args[0]


@pytest.fixture
def direct_drag():
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    # There is intentionally no Window, frame driver, or visible surface.
    component.setData(b'''import QtQuick
        QtObject {
            property var bridge
            property bool manualDragActive: true
            property bool nativeDragAwaitingThreshold: false
            property bool nativeSystemMoveActive: false
            property bool nativeSystemMoveStartPending: false
            property int nativeSystemMoveGestureSerial: 7
            property int consumed: 0
            property int updates: 0
            property real pointerX: 0
            function followCapturedPointerEvent() {
                let p = bridge.takeLatestPointerEvent(consumed)
                if (!p.available) return false
                consumed = p.serial
                pointerX = p.x
                updates += 1
                return true
            }
        }''', QUrl())
    root = component.create()
    assert root, component.errors()
    bridge = CompactPointerEventFilter(root)
    root.setProperty("bridge", bridge)
    yield root, bridge
    bridge._direct_pointer_timer.stop()
    root.setProperty("manualDragActive", False)
    root.deleteLater()
    QCoreApplication.processEvents()


def send_move(root, bridge, x):
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(5, 5), QPointF(x, 100),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    assert bridge.eventFilter(root, event)


def test_direct_drag_wakes_qml_without_a_render_frame_and_coalesces(direct_drag):
    root, bridge = direct_drag
    for index in range(1000):
        send_move(root, bridge, index)
    assert root.property("updates") == 0
    assert bridge._direct_pointer_timer.isActive()
    QCoreApplication.processEvents()
    assert root.property("updates") == 1
    assert root.property("pointerX") == 999
    assert bridge._direct_pointer_flushes == 1
    send_move(root, bridge, 1200)
    QCoreApplication.processEvents()
    assert root.property("updates") == 2
    assert root.property("pointerX") == 1200


@pytest.mark.parametrize("property_name,value", [
    ("manualDragActive", False),
    ("nativeSystemMoveActive", True),
    ("nativeSystemMoveStartPending", True),
    ("nativeSystemMoveGestureSerial", 8),
])
def test_queued_movement_cannot_outlive_its_gesture(direct_drag, property_name, value):
    root, bridge = direct_drag
    send_move(root, bridge, 800)
    root.setProperty(property_name, value)
    QCoreApplication.processEvents()
    assert root.property("updates") == 0


def test_release_cancels_pending_wakeup_but_preserves_final_point(direct_drag):
    root, bridge = direct_drag
    send_move(root, bridge, 500)
    event = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), QPointF(700, 100),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    assert not bridge.eventFilter(root, event)
    QCoreApplication.processEvents()
    assert root.property("updates") == 0
    assert bridge.takeLatestPointerEvent(0)["x"] == 700
