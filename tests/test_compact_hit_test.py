from __future__ import annotations

import ctypes
import json
import math
import os
import threading
from ctypes import wintypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

from lilies.app import (
    CompactHitTestFilter,
    CompactPointerEventFilter,
    _LatestJsonFileWriter,
    _SystemMoveWatcher,
)


class _Root:
    def __init__(self) -> None:
        self.values = {
            "compactExpanded": False,
            "compactActionsInteractive": False,
            "compactCharacterLeft": 100,
            "compactCharacterTop": 80,
            "compactCharacterWidth": 120,
            "compactCharacterHeight": 300,
            "compactCharacterHitTolerance": 6,
            "compactAccessoryLeft": 260,
            "compactAccessoryTop": 250,
            "compactAccessoryWidth": 80,
        }

    def property(self, key):
        return self.values[key]


class _Backend:
    class Shell:
        mode = "compact"

    shell = Shell()


def test_compact_window_only_accepts_visible_pet_regions():
    root = _Root()
    hit_test = CompactHitTestFilter(root, _Backend(), native_window_id=4242)
    assert hit_test.accepts_point(160, 200)
    assert hit_test.accepts_point(300, 290)
    assert not hit_test.accepts_point(20, 20)
    root.values["compactExpanded"] = True
    root.values["compactActionsInteractive"] = True
    # Expanding the radial menu must not turn the large transparent tool
    # window into an input shield. Only real visible menu items are accepted.
    assert not hit_test.accepts_point(20, 20)


def test_physical_hit_test_point_is_scaled_to_qml_coordinates():
    # The current machine uses 150% scaling. A physical click at (240, 300)
    # corresponds to logical (160, 200), which is inside the character.
    logical = CompactHitTestFilter._logical_point(240, 300, 1.5)
    assert logical == (160, 200)
    assert CompactHitTestFilter(
        _Root(), _Backend(), native_window_id=4242
    ).accepts_point(*logical)


def test_native_hit_test_uses_character_silhouette_when_available():
    root = _Root()
    root.characterContains = lambda x, y: 135 <= x <= 185 and 100 <= y <= 360
    hit_test = CompactHitTestFilter(root, _Backend(), native_window_id=4242)
    assert hit_test.accepts_point(160, 200)
    assert not hit_test.accepts_point(105, 85)


def test_native_character_broad_phase_includes_only_bounded_mask_tolerance():
    root = _Root()
    observed: list[tuple[float, float]] = []

    def interaction_mask(x: float, y: float) -> bool:
        observed.append((x, y))
        return 94 <= x <= 226 and 74 <= y <= 386

    root.characterContains = interaction_mask
    hit_test = CompactHitTestFilter(root, _Backend(), native_window_id=4242)

    assert hit_test.accepts_point(95, 200)
    assert observed[-1] == (95, 200)
    calls_after_near_edge = len(observed)
    assert not hit_test.accepts_point(93, 200)
    assert len(observed) == calls_after_near_edge
    assert not hit_test.accepts_point(20, 20)

    # The expanded rectangle is only a cheap prefilter. A transparent point
    # within it must still pass through if the QML silhouette rejects it.
    root.characterContains = lambda _x, _y: False
    assert not hit_test.accepts_point(95, 200)


def test_character_hit_tolerance_uses_logical_coordinates_at_high_dpi():
    root = _Root()
    root.characterContains = lambda x, y: 94 <= x <= 226 and 74 <= y <= 386
    hit_test = CompactHitTestFilter(root, _Backend(), native_window_id=4242)

    near = CompactHitTestFilter._logical_point(95 * 1.5, 200 * 1.5, 1.5)
    beyond = CompactHitTestFilter._logical_point(93 * 1.5, 200 * 1.5, 1.5)
    assert hit_test.accepts_point(*near)
    assert not hit_test.accepts_point(*beyond)


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows MSG bridge")
def test_native_filter_never_resolves_win_id_while_dispatching():
    class _NativeRoot(_Root):
        def __init__(self) -> None:
            super().__init__()
            self.win_id_calls = 0

        def winId(self):
            self.win_id_calls += 1
            return 4242

    root = _NativeRoot()
    hit_test = CompactHitTestFilter(root, _Backend(), native_window_id=4242)
    assert root.win_id_calls == 0
    root.winId = lambda: (_ for _ in ()).throw(
        AssertionError("native callback re-entered QWindow.winId")
    )

    message = wintypes.MSG()
    message.hWnd = 4242
    # The real startup recursion was reached through WM_NCHITTEST. The dummy
    # HWND makes GetWindowRect fail safely after the cached-handle comparison.
    message.message = CompactHitTestFilter.WM_NCHITTEST
    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(message)
    ) == (False, 0)
    # A native callback must only compare the cached integer. Calling winId()
    # again here can recurse through QWindowPrivate::create on a real startup.
    assert root.win_id_calls == 0


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows MSG bridge")
def test_native_character_press_is_claimed_before_qml_mouse_delivery():
    class _NativePressRoot(_Root):
        def devicePixelRatio(self):
            return 1.5

        def x(self):
            return -320.0

        def y(self):
            return 140.0

    class _NativePressBackend(_Backend):
        petDragMode = "system"

    class _NativePressController:
        def __init__(self):
            self.queued = []
            self.pointer_packets = 0
            self.releases = 0

        def cachedCharacterHit(
            self,
            px,
            py,
            tolerance,
            _semantic_key,
            _geometry_key,
        ):
            return 94 <= px <= 226 and 74 <= py <= 386

        def queueNativeCharacterPress(
            self, global_x, global_y, physical_x, physical_y
        ):
            if self.queued:
                return False
            self.queued.append(
                (
                    float(global_x),
                    float(global_y),
                    float(physical_x),
                    float(physical_y),
                )
            )
            return True

        def recordQueuedNativeCharacterPointer(self, _physical_x, _physical_y):
            self.pointer_packets += 1
            return True

        @property
        def native_character_press_active(self):
            return bool(self.queued)

        @property
        def native_character_prestart_active(self):
            return bool(self.queued) and self.releases == 0

        def handleNativeCharacterRelease(self):
            self.releases += 1
            return True

    root = _NativePressRoot()
    controller = _NativePressController()
    hit_test = CompactHitTestFilter(
        root,
        _NativePressBackend(),
        native_window_id=4242,
        native_move_controller=controller,
    )

    # Client coordinates arrive in physical pixels.  (240, 300) maps to the
    # logical point (160, 200), so the queued global point is (-160, 340).
    press = wintypes.MSG()
    press.hWnd = 4242
    press.message = CompactHitTestFilter.WM_LBUTTONDOWN
    press.lParam = (300 << 16) | 240
    press.pt.x = -80
    press.pt.y = 440
    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(press)
    ) == (True, 0)
    assert controller.queued == [(-160.0, 340.0, -80.0, 440.0)]

    move = wintypes.MSG()
    move.hWnd = 4242
    move.message = CompactHitTestFilter.WM_MOUSEMOVE
    move.lParam = (330 << 16) | 270
    move.pt.x = -50
    move.pt.y = 470
    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(move)
    ) == (True, 0)
    assert controller.pointer_packets == 1

    release = wintypes.MSG()
    release.hWnd = 4242
    release.message = CompactHitTestFilter.WM_LBUTTONUP
    release.lParam = move.lParam
    release.pt.x = move.pt.x
    release.pt.y = move.pt.y
    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(release)
    ) == (True, 0)
    assert controller.releases == 1
    assert controller.pointer_packets == 2

    move.pt.x = 900
    move.pt.y = 900
    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(move)
    ) == (False, 0)
    assert controller.pointer_packets == 2

    # Windows reports the second press of a double-click as DBLCLK, not DOWN.
    # It must enter the same owner instead of falling back to a second QML
    # gesture half way through the sequence.
    press.message = CompactHitTestFilter.WM_LBUTTONDBLCLK
    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(press)
    ) == (True, 0)
    assert controller.queued == [(-160.0, 340.0, -80.0, 440.0)]


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows MSG bridge")
def test_native_hover_skips_prestart_coordinate_mapping() -> None:
    hit_test = CompactHitTestFilter(
        _Root(), _Backend(), native_window_id=4242
    )

    class _IdleController:
        native_character_prestart_active = False

        def recordQueuedNativeCharacterPointer(self, _x, _y):
            raise AssertionError("idle hover must not enter the recorder")

    hit_test.native_move_controller = _IdleController()
    hit_test._native_client_global_point = lambda _message: (_ for _ in ()).throw(
        AssertionError("idle hover must not map coordinates")
    )
    move = wintypes.MSG()
    move.hWnd = 4242
    move.message = CompactHitTestFilter.WM_MOUSEMOVE
    move.lParam = (330 << 16) | 270

    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(move)
    ) == (False, 0)


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows MSG bridge")
def test_native_character_press_does_not_claim_accessory_or_direct_mode():
    class _NativePressRoot(_Root):
        def devicePixelRatio(self):
            return 1.0

        def x(self):
            return 0.0

        def y(self):
            return 0.0

    class _NativePressBackend(_Backend):
        petDragMode = "system"

    class _NativePressController:
        def __init__(self):
            self.queued = []

        def cachedCharacterHit(
            self,
            _px,
            _py,
            _tolerance,
            _semantic_key,
            _geometry_key,
        ):
            return True

        def queueNativeCharacterPress(
            self, global_x, global_y, physical_x, physical_y
        ):
            self.queued.append(
                (global_x, global_y, physical_x, physical_y)
            )
            return True

    root = _NativePressRoot()
    # Force a real overlap: z ordering must leave the accessory in control.
    root.values["compactAccessoryLeft"] = 130
    root.values["compactAccessoryTop"] = 160
    controller = _NativePressController()
    backend = _NativePressBackend()
    hit_test = CompactHitTestFilter(
        root,
        backend,
        native_window_id=4242,
        native_move_controller=controller,
    )
    message = wintypes.MSG()
    message.hWnd = 4242
    message.message = CompactHitTestFilter.WM_LBUTTONDOWN
    message.lParam = (200 << 16) | 160

    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(message)
    ) == (False, 0)
    assert controller.queued == []

    root.values["compactAccessoryLeft"] = 260
    root.values["compactAccessoryTop"] = 250
    hit_test._refresh_native_hit_snapshot()
    backend.petDragMode = "direct"
    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(message)
    ) == (False, 0)
    assert controller.queued == []


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows MSG bridge")
def test_native_down_and_up_never_call_qml_before_user32_returns():
    app = QCoreApplication.instance() or QCoreApplication([])

    class _DeferredRoot(_QueuedNativePressRoot):
        def __init__(self):
            super().__init__()
            self.native_values = {
                "compactExpanded": False,
                "compactActionsInteractive": False,
                "compactCharacterLeft": 100,
                "compactCharacterTop": 80,
                "compactCharacterWidth": 120,
                "compactCharacterHeight": 300,
                "compactCharacterHitTolerance": 6,
                "compactAccessoryLeft": 260,
                "compactAccessoryTop": 250,
                "compactAccessoryWidth": 80,
                "compactDragSnapshotKey": "snapshot-a",
                "compactDragGeometryKey": "geometry-a",
            }

        def property(self, key):
            if key in self.native_values:
                return self.native_values[key]
            return super().property(key)

        def x(self):
            return 25.0

        def y(self):
            return 30.0

    class _NativePressBackend(_Backend):
        petDragMode = "system"

    root = _DeferredRoot()
    controller = CompactPointerEventFilter(root)
    controller._left_button_is_down = lambda: False
    controller._drag_proxy_cache = SimpleNamespace(
        cached_alpha_contains=lambda *_args, **_kwargs: True
    )
    hit_test = CompactHitTestFilter(
        root,
        _NativePressBackend(),
        native_window_id=4242,
        native_move_controller=controller,
    )
    press = wintypes.MSG()
    press.hWnd = 4242
    press.message = CompactHitTestFilter.WM_LBUTTONDOWN
    press.lParam = (200 << 16) | 160
    release = wintypes.MSG()
    release.hWnd = 4242
    release.message = CompactHitTestFilter.WM_LBUTTONUP

    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(press)
    ) == (True, 0)
    assert root.begin_calls == []
    assert root.start_calls == []
    assert root.finish_calls == []
    assert hit_test.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(release)
    ) == (True, 0)
    assert root.begin_calls == []
    assert root.finish_calls == []

    app.processEvents()
    assert root.begin_calls == [(185.0, 230.0)]
    assert root.start_calls == []
    assert root.finish_calls == [91]


class _VisualItem:
    def __init__(self, name="", children=None):
        self._name = name
        self._children = list(children or [])

    def childItems(self):
        return self._children


def test_visual_descendants_include_repeater_style_delegates():
    action = _VisualItem("desktopPetAction_chat")
    repeater_host = _VisualItem(children=[action])
    content = _VisualItem(children=[repeater_host])

    descendants = CompactHitTestFilter._visual_descendants(content)

    assert action in descendants


def test_pointer_event_filter_preserves_event_time_global_position():
    root = QObject()
    root.setProperty("capturedPointerEventSerial", 0)
    root.setProperty("manualDragActive", True)
    event_filter = CompactPointerEventFilter(root)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(312.0, 118.0),
        QPointF(-845.5, 731.25),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert event_filter.eventFilter(root, event) is True
    # The raw event no longer writes three QML properties. The drag frame asks
    # for one newest native sample regardless of mouse polling rate.
    assert root.property("capturedPointerEventSerial") == 0
    latest = event_filter.takeLatestPointerEvent(0)
    assert latest == {
        "available": True,
        "serial": 1,
        "x": -845.5,
        "y": 731.25,
    }
    assert event_filter.takeLatestPointerEvent(1) == {
        "available": False,
        "serial": 1,
    }


def test_high_rate_native_pointer_stream_keeps_only_the_newest_sample():
    root = QObject()
    root.setProperty("manualDragActive", True)
    event_filter = CompactPointerEventFilter(root)

    for index in range(1000):
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(20.0, 30.0),
            QPointF(float(index), float(index + 10)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        assert event_filter.eventFilter(root, event) is True

    assert event_filter.takeLatestPointerEvent(0) == {
        "available": True,
        "serial": 1000,
        "x": 999.0,
        "y": 1009.0,
    }


def test_system_owned_pointer_stream_skips_all_sampling_work():
    root = _NativeMoveRoot()
    root.setProperty("manualDragActive", True)
    event_filter = CompactPointerEventFilter(root)
    assert _start_qt_system_move_compatibility(event_filter, 71) is True

    for index in range(1000):
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(20.0, 30.0),
            QPointF(float(index), float(index + 10)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        assert event_filter.eventFilter(root, event) is True

    assert event_filter.takeLatestPointerEvent(0) == {
        "available": False,
        "serial": 0,
    }
    event_filter.acknowledgeSystemMoveFinished(71)


def test_active_manual_drag_consumes_only_moves_and_keeps_release_point():
    root = QObject()
    root.setProperty("manualDragActive", False)
    event_filter = CompactPointerEventFilter(root)

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(40.0, 50.0),
        QPointF(440.0, 550.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert event_filter.eventFilter(root, press) is False

    # QML accepts the press and marks the gesture active after the event
    # filter returns. Every subsequent raw move stays in the native coalescer
    # instead of also traversing the QML MouseArea tree.
    root.setProperty("manualDragActive", True)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(48.0, 53.0),
        QPointF(448.0, 553.0),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert event_filter.eventFilter(root, move) is True

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(52.0, 55.0),
        QPointF(452.0, 555.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert event_filter.eventFilter(root, release) is False
    assert event_filter.takeLatestPointerEvent(0) == {
        "available": True,
        "serial": 3,
        "x": 452.0,
        "y": 555.0,
    }


def test_manual_drag_does_not_consume_double_click_delivery():
    root = QObject()
    root.setProperty("manualDragActive", True)
    event_filter = CompactPointerEventFilter(root)
    double_click = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(25.0, 30.0),
        QPointF(325.0, 430.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert event_filter.eventFilter(root, double_click) is False
    assert event_filter.takeLatestPointerEvent(0)["serial"] == 1


class _NativeMoveRoot(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.completions = []
        self.positions = []

    def startSystemMove(self):
        return True

    def finishNativeSystemMove(self, gesture_serial):
        self.completions.append(int(gesture_serial))

    def setPosition(self, point):
        self.positions.append(QPoint(point))

    def width(self):
        return 420

    def height(self):
        return 396

    def devicePixelRatio(self):
        return 1.5

    def screen(self):
        return None

    def winId(self):
        return 4321


def _start_qt_system_move_compatibility(
    event_filter: CompactPointerEventFilter,
    gesture_serial: int,
) -> bool:
    """Exercise the retained non-Windows Qt compatibility branch on Windows.

    Production Windows tests use the sentinel-backed layered proxy. Tests for
    QML/filter/diagnostic bookkeeping that do not construct that proxy still
    need a deterministic way to cover Qt's cross-platform startSystemMove
    result without re-enabling the unsafe Windows fallback.
    """

    original_name = os.name
    try:
        os.name = "posix"
        return event_filter.tryStartSystemMove(gesture_serial)
    finally:
        os.name = original_name


class _QueuedNativePressRoot(QObject):
    def __init__(self, *, start_result: bool = True) -> None:
        super().__init__()
        self.setProperty("manualDragActive", False)
        self.start_result = bool(start_result)
        self.begin_calls: list[tuple[float, float]] = []
        self.prime_calls: list[int] = []
        self.latch_calls: list[int] = []
        self.start_calls: list[int] = []
        self.finish_calls: list[int] = []
        self.cancel_calls: list[int] = []

    def devicePixelRatio(self) -> float:
        return 1.0

    def beginNativeCharacterPress(self, global_x: float, global_y: float) -> int:
        self.begin_calls.append((float(global_x), float(global_y)))
        self.setProperty("manualDragActive", True)
        return 91

    def startQueuedNativeCharacterPress(self, serial: int) -> bool:
        self.start_calls.append(int(serial))
        return self.start_result

    def primeQueuedNativeCharacterPress(self, serial: int) -> bool:
        self.prime_calls.append(int(serial))
        return True

    def latchQueuedNativeCharacterMotion(self, serial: int) -> bool:
        self.latch_calls.append(int(serial))
        return True

    def finishQueuedNativeCharacterPress(self, serial: int) -> bool:
        self.finish_calls.append(int(serial))
        self.setProperty("manualDragActive", False)
        return True

    def cancelQueuedNativeCharacterPress(self, serial: int) -> bool:
        self.cancel_calls.append(int(serial))
        self.setProperty("manualDragActive", False)
        return True


def test_native_press_queue_never_calls_qml_on_the_native_callback_turn() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _QueuedNativePressRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._left_button_is_down = lambda: True

    assert event_filter.queueNativeCharacterPress(420.0, 315.0) is True
    assert root.begin_calls == []
    assert root.start_calls == []

    app.processEvents()

    assert root.begin_calls == [(420.0, 315.0)]
    assert root.start_calls == [91]
    assert event_filter._queued_native_character_request_id == 0


def test_release_before_queued_start_is_preserved_as_stationary_click() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _QueuedNativePressRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._left_button_is_down = lambda: False

    assert event_filter.queueNativeCharacterPress(120.0, 85.0) is True
    assert event_filter.handleNativeCharacterRelease() is True
    assert root.begin_calls == []
    assert root.finish_calls == []
    app.processEvents()

    assert root.begin_calls == [(120.0, 85.0)]
    assert root.start_calls == []
    assert root.finish_calls == [91]
    assert root.property("manualDragActive") is False


def test_motion_before_queued_start_is_primed_once_before_native_move() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _QueuedNativePressRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._left_button_is_down = lambda: True

    event_filter._logical_global_from_physical_screen = lambda x, y: (x, y)
    assert event_filter.queueNativeCharacterPress(
        120.0, 85.0, 120.0, 85.0
    ) is True
    assert event_filter.recordQueuedNativeCharacterPointer(156.0, 109.0) is True
    app.processEvents()

    assert root.begin_calls == [(120.0, 85.0)]
    assert root.prime_calls == [91]
    assert root.latch_calls == [91]
    assert root.start_calls == [91]
    assert event_filter._diagnostic_prestart_pointer_samples == 1
    assert event_filter._diagnostic_prestart_max_distance_logical == pytest.approx(
        math.hypot(36.0, 24.0)
    )


def test_fast_motion_release_before_queued_start_keeps_latest_point() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _QueuedNativePressRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._left_button_is_down = lambda: False

    event_filter._logical_global_from_physical_screen = lambda x, y: (x, y)
    assert event_filter.queueNativeCharacterPress(
        120.0, 85.0, 120.0, 85.0
    ) is True
    assert event_filter.recordQueuedNativeCharacterPointer(180.0, 125.0) is True
    assert event_filter.handleNativeCharacterRelease() is True
    app.processEvents()

    assert root.begin_calls == [(120.0, 85.0)]
    assert root.prime_calls == []
    assert root.start_calls == []
    assert root.latch_calls == [91]
    assert root.finish_calls == [91]
    assert event_filter._diagnostic_release_before_queued_start is True
    assert event_filter._diagnostic_prestart_pointer_samples == 1
    assert event_filter._diagnostic_prestart_max_distance_logical == pytest.approx(
        math.hypot(60.0, 40.0)
    )
    assert event_filter.takeLatestPointerEvent(0) == {
        "available": True,
        "serial": 1,
        "x": 180.0,
        "y": 125.0,
    }


def test_physical_screen_point_maps_across_qt_dpi_islands() -> None:
    class _Screen:
        def __init__(self, rect: QRect, dpr: float) -> None:
            self._rect = rect
            self._dpr = dpr

        def geometry(self) -> QRect:
            return self._rect

        def devicePixelRatio(self) -> float:
            return self._dpr

    screens = [
        _Screen(QRect(0, 0, 2560, 1600), 1.5),
        _Screen(QRect(3840, 0, 1920, 1080), 1.0),
        _Screen(QRect(-1920, -1080, 1080, 1920), 1.0),
    ]
    mapper = CompactPointerEventFilter._logical_global_from_physical_screen

    assert mapper(1500.0, 900.0, screens) == pytest.approx((1000.0, 600.0))
    assert mapper(4320.0, 540.0, screens) == pytest.approx((4320.0, 540.0))
    assert mapper(-1500.0, -900.0, screens) == pytest.approx((-1500.0, -900.0))


def test_release_closes_prestart_pointer_stream_before_next_move() -> None:
    root = _QueuedNativePressRoot()
    event_filter = CompactPointerEventFilter(root)

    assert event_filter.queueNativeCharacterPress(
        120.0, 85.0, 120.0, 85.0
    ) is True
    assert event_filter.recordQueuedNativeCharacterPointer(180.0, 125.0)
    assert event_filter.handleNativeCharacterRelease() is True
    before = event_filter._queued_native_character_latest_physical

    assert event_filter.native_character_prestart_active is False
    assert event_filter.recordQueuedNativeCharacterPointer(900.0, 900.0) is False
    assert event_filter._queued_native_character_latest_physical == before


def test_prestart_move_out_and_back_stays_a_drag() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _QueuedNativePressRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._logical_global_from_physical_screen = lambda x, y: (x, y)
    event_filter._left_button_is_down = lambda: False

    assert event_filter.queueNativeCharacterPress(
        120.0, 85.0, 120.0, 85.0
    ) is True
    assert event_filter.recordQueuedNativeCharacterPointer(150.0, 85.0)
    assert event_filter.recordQueuedNativeCharacterPointer(120.0, 85.0)
    assert event_filter.handleNativeCharacterRelease() is True
    app.processEvents()

    assert root.latch_calls == [91]
    assert root.finish_calls == [91]
    assert event_filter._diagnostic_prestart_motion_latched is True
    assert event_filter._diagnostic_prestart_max_distance_logical == 0.0
    assert event_filter._diagnostic_prestart_max_distance_physical == 30.0


def test_failed_native_start_release_watchdog_cannot_strand_gesture() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _QueuedNativePressRoot(start_result=False)
    event_filter = CompactPointerEventFilter(root)
    event_filter._left_button_is_down = lambda: True

    assert event_filter.queueNativeCharacterPress(120.0, 85.0) is True
    app.processEvents()
    assert root.property("manualDragActive") is True
    assert event_filter.native_character_press_active is True

    event_filter._left_button_is_down = lambda: False
    event_filter._poll_queued_native_character_release()
    assert root.finish_calls == []
    app.processEvents()

    assert root.finish_calls == [91]
    assert root.property("manualDragActive") is False
    assert event_filter.native_character_press_active is False


def test_swapped_primary_button_drives_raw_press_release_probe() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _QueuedNativePressRoot()
    event_filter = CompactPointerEventFilter(root)

    class User32:
        def __init__(self) -> None:
            self.keys: list[int] = []

        def GetSystemMetrics(self, metric: int) -> int:
            assert metric == CompactPointerEventFilter._SM_SWAPBUTTON
            return 1

        def GetAsyncKeyState(self, key: int) -> int:
            self.keys.append(int(key))
            return 0

    user32 = User32()
    event_filter._win32_user32 = lambda: user32

    assert event_filter.queueNativeCharacterPress(120.0, 85.0) is True
    app.processEvents()

    assert user32.keys == [CompactPointerEventFilter._VK_RBUTTON]
    assert root.start_calls == []
    assert root.finish_calls == [91]


def test_swapped_primary_button_drives_native_move_watchdog() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)

    class User32:
        def __init__(self) -> None:
            self.keys: list[int] = []

        def GetSystemMetrics(self, metric: int) -> int:
            assert metric == CompactPointerEventFilter._SM_SWAPBUTTON
            return 1

        def GetAsyncKeyState(self, key: int) -> int:
            self.keys.append(int(key))
            return 0

    user32 = User32()
    event_filter._win32_user32 = lambda: user32

    assert _start_qt_system_move_compatibility(event_filter, 92) is True
    event_filter._poll_system_move_release()
    app.processEvents()

    assert user32.keys == [CompactPointerEventFilter._VK_RBUTTON]
    assert root.completions == [92]
    assert event_filter.native_system_move_active is False


def test_capture_loss_cancels_queued_native_character_gesture() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _QueuedNativePressRoot(start_result=False)
    event_filter = CompactPointerEventFilter(root)
    event_filter._left_button_is_down = lambda: True

    assert event_filter.queueNativeCharacterPress(120.0, 85.0) is True
    app.processEvents()
    assert event_filter.cancelNativeCharacterPress() is True
    assert root.cancel_calls == []
    app.processEvents()

    assert root.cancel_calls == [91]
    assert root.property("manualDragActive") is False
    assert event_filter.native_character_press_active is False


class _ProxyMoveRoot(_NativeMoveRoot):
    frameSwapped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.opacity_value = 1.0
        self.opacity_writes: list[float] = []
        self.system_move_starts = 0
        self.update_requests = 0
        self.logical_visible = True

    def property(self, key):
        if key == "compactDragSnapshotKey":
            return "pose|size|box"
        if key == "compactDragGeometryKey":
            return "body-geometry"
        return super().property(key)

    def opacity(self):
        return self.opacity_value

    def setOpacity(self, value):
        self.opacity_value = float(value)
        self.opacity_writes.append(float(value))

    def startSystemMove(self):
        self.system_move_starts += 1
        return True

    def requestUpdate(self):
        self.update_requests += 1

    def isVisible(self):
        return self.logical_visible


class _ProxyCache:
    def __init__(
        self,
        *,
        prepared: bool = True,
        start_move_succeeds: bool | None = None,
    ) -> None:
        self.prepared = prepared
        self.start_move_succeeds = (
            prepared
            if start_move_succeeds is None
            else bool(start_move_succeeds)
        )
        self.last_failure = "stale-key" if not prepared else ""
        self.last_prepare_used_stale_visual = False
        self.cache_age_ms = 18.5
        self.metadata = SimpleNamespace(pixel_size=QSize(180, 240))
        self.proxy_handle = 2**48 + 101
        self.active = False
        self.completed = 0
        self.cancelled = 0
        self.native_move_cancels = 0
        self.gesture_active = False
        self.final = SimpleNamespace(
            rect=SimpleNamespace(left=120, top=240),
            delta=SimpleNamespace(x=0, y=0),
        )

    def begin_gesture(self):
        self.gesture_active = True

    def end_gesture(self):
        self.gesture_active = False

    def prepare(self, key, root_rect, geometry_key):
        assert key == "pose|size|box"
        assert geometry_key == "body-geometry"
        assert root_rect.left == 100 and root_rect.top == 200
        if not self.prepared:
            return 0
        self.active = True
        return 2**48 + 101

    def start_move(self):
        if not self.start_move_succeeds:
            self.last_failure = "move-request-failed"
            self.active = False
            return False
        return self.prepared

    def preview_final(self):
        return self.final if self.active else None

    def complete(self):
        self.completed += 1
        self.active = False

    def cancel(self):
        self.cancelled += 1
        self.active = False

    def cancel_native_move(self):
        self.native_move_cancels += 1
        return self.active

    def close(self):
        self.active = False


def test_startup_proxy_prewarm_uses_current_qml_keys_only_when_visible() -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._drag_proxy_cache = SimpleNamespace(active=False)
    requests: list[tuple[str, str]] = []
    event_filter.requestDragProxySnapshot = (
        lambda semantic, geometry="": not requests.append(
            (str(semantic), str(geometry))
        )
    )

    event_filter._prewarm_drag_proxy_snapshot()
    assert requests == [("pose|size|box", "body-geometry")]

    root.logical_visible = False
    event_filter._prewarm_drag_proxy_snapshot()
    assert requests == [("pose|size|box", "body-geometry")]


def test_ready_startup_proxy_publishes_bitmap_and_clears_cache_miss() -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._drag_proxy_cache = _ProxyCache()
    event_filter._proxy_fallback_reason = "cache-miss"
    states: list[dict[str, object]] = []
    event_filter.dragProxyRuntimeStateChanged.connect(states.append)

    event_filter.publishDragProxyRuntimeState()

    assert states[-1]["ready"] is True
    assert states[-1]["proxyBitmapWidth"] == 180
    assert states[-1]["proxyBitmapHeight"] == 240
    assert states[-1]["proxyCacheAgeMs"] == 18.5
    assert states[-1]["fallbackReason"] == ""


class _NativeFilterApp:
    def __init__(self, *, failed_install_attempts: int = 0) -> None:
        self.removed: list[object] = []
        self.installed: list[object] = []
        self.failed_install_attempts = int(failed_install_attempts)

    def removeNativeEventFilter(self, value) -> None:
        self.removed.append(value)

    def installNativeEventFilter(self, value) -> None:
        if self.failed_install_attempts > 0:
            self.failed_install_attempts -= 1
            raise RuntimeError("platform object is being recreated")
        self.installed.append(value)


class _QtFilterRetryRoot(_NativeMoveRoot):
    def __init__(self) -> None:
        super().__init__()
        self.failed_qt_install_attempts = 1
        self.qt_installs: list[object] = []

    def installEventFilter(self, value) -> None:
        if self.failed_qt_install_attempts > 0:
            self.failed_qt_install_attempts -= 1
            raise RuntimeError("QWindow event dispatcher is being recreated")
        self.qt_installs.append(value)


def test_system_move_suspends_python_native_filter_until_completion() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(app, native_filter)

    assert _start_qt_system_move_compatibility(event_filter, 35) is True
    assert app.removed == [native_filter]
    assert app.installed == []
    assert event_filter._native_drag_filter_suspended is True
    assert event_filter._qt_pointer_filter_suspended is True

    event_filter.acknowledgeSystemMoveFinished(35)
    assert app.installed == [native_filter]
    assert event_filter._native_drag_filter_suspended is False
    assert event_filter._qt_pointer_filter_suspended is False


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_cached_proxy_moves_only_preview_then_commits_real_window_once(
    monkeypatch,
) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    root_window_id = 2**48 + 77
    root.winId = lambda: root_window_id
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=root_window_id
    )
    cache = _ProxyCache()
    cache.last_prepare_used_stale_visual = True
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )
    commits: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_position",
        lambda window_id, x, y: not commits.append(
            (int(window_id), int(x), int(y))
        ),
    )
    native_visibility: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_shown",
        lambda window_id, shown: not native_visibility.append(
            (int(window_id), bool(shown))
        ),
    )

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(361) is True
    assert root.system_move_starts == 0
    assert root.opacity_value == 1.0
    assert root.opacity_writes == []
    assert native_visibility == [(root_window_id, False)]
    assert event_filter._active_system_move_window_id == 2**48 + 101

    cache.final = SimpleNamespace(
        rect=SimpleNamespace(left=157, top=269),
        delta=SimpleNamespace(x=37, y=29),
    )
    assert event_filter._commit_proxy_geometry() is True
    assert commits == [(root_window_id, 137, 229)]
    assert root.opacity_value == 1.0
    assert root.opacity_writes == []
    assert native_visibility == [(root_window_id, False), (root_window_id, True)]
    assert root.update_requests == 1
    assert event_filter._proxy_real_geometry_commits == 1
    event_filter.acknowledgeSystemMoveFinished(361)
    assert cache.gesture_active is False

    report = event_filter.dragDiagnosticsSnapshot()
    assert report["mode"] == "layered-proxy"
    assert report["proxyRealGeometryCommits"] == 1
    assert report["proxyBitmapWidth"] == 180
    assert report["proxyBitmapHeight"] == 240
    assert report["proxyCacheAgeMs"] == 18.5
    assert report["proxyVisualStale"] is True
    assert report["proxyRootNativeHidden"] is True
    # A queued frameSwapped from a previous restore cannot be attributed to
    # this proxy generation, so only the generation-bound timer (or its exact
    # completion helper in this test) may retire the preview.
    root.frameSwapped.emit()
    assert cache.completed == 0
    assert event_filter._proxy_preview_hide_timer.isActive() is True
    assert event_filter._complete_proxy_preview() is True
    assert cache.completed == 1
    assert event_filter._proxy_preview_hide_timer.isActive() is False


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_synchronous_capture_cancel_retires_proxy_before_start_returns(
    monkeypatch,
) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    root_window_id = 2**48 + 177
    root.winId = lambda: root_window_id
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=root_window_id
    )
    cache = _ProxyCache()
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )
    native_visibility: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_shown",
        lambda window_id, shown: not native_visibility.append(
            (int(window_id), bool(shown))
        ),
    )
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_position",
        lambda _window_id, _x, _y: True,
    )
    original_start = cache.start_move

    def reentrant_start() -> bool:
        # Mirrors ReleaseCapture -> QML finish/ack while Python's proxy start
        # call is still on the stack.
        event_filter.acknowledgeSystemMoveFinished(501)
        return original_start()

    cache.start_move = reentrant_start

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(501) is False

    assert event_filter._active_system_move_session_id == 0
    assert event_filter.native_system_move_active is False
    assert event_filter._proxy_move_active is False
    assert event_filter._proxy_move_session_id == 0
    assert event_filter._proxy_real_geometry_commits == 1
    assert cache.native_move_cancels == 1
    assert native_visibility == [
        (root_window_id, False),
        (root_window_id, True),
    ]
    # The successful return from the already-posted request did not resurrect
    # the retired session.
    event_filter._on_system_move_started(cache.proxy_handle, 1)
    event_filter._on_system_move_ended(cache.proxy_handle, 1)
    assert event_filter.native_system_move_active is False


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_no_start_or_end_event_still_commits_and_restores_after_button_up(
    monkeypatch,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    root_window_id = 2**48 + 178
    root.winId = lambda: root_window_id
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=root_window_id
    )
    cache = _ProxyCache()
    cache.final = SimpleNamespace(
        rect=SimpleNamespace(left=148, top=271),
        delta=SimpleNamespace(x=28, y=31),
    )
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )
    commits: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_position",
        lambda window_id, x, y: not commits.append(
            (int(window_id), int(x), int(y))
        ),
    )
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_shown",
        lambda _window_id, _shown: True,
    )
    event_filter._left_button_is_down = lambda: False

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(502) is True
    assert event_filter._system_move_entered is False
    assert event_filter._system_move_exited is False

    event_filter._poll_system_move_release()
    app.processEvents()

    assert event_filter.native_system_move_active is False
    assert event_filter._active_system_move_session_id == 0
    assert event_filter._proxy_move_active is False
    assert event_filter._proxy_root_native_hidden is False
    assert event_filter._proxy_real_geometry_commits == 1
    assert commits == [(root_window_id, 128, 231)]
    assert root.completions == [502]
    assert event_filter._completion_queued_by == "button-release-watchdog"


def test_late_old_completion_cannot_retire_or_resurrect_new_session() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)

    assert _start_qt_system_move_compatibility(event_filter, 601) is True
    first_session = event_filter._active_system_move_session_id
    event_filter.acknowledgeSystemMoveFinished(601)
    assert _start_qt_system_move_compatibility(event_filter, 602) is True
    second_session = event_filter._active_system_move_session_id
    assert second_session != first_session

    event_filter._deliverSystemMoveFinished(first_session, 601)
    event_filter._on_system_move_started(4321, first_session)
    event_filter._on_system_move_ended(4321, first_session)

    assert event_filter._active_system_move_session_id == second_session
    assert event_filter._active_system_move_serial == 602
    event_filter.acknowledgeSystemMoveFinished(602)
    assert event_filter.native_system_move_active is False


def test_layered_proxy_ignores_all_same_hwnd_winevents_across_generations() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    session_id = event_filter._new_system_move_session(611)
    proxy_window_id = 2**48 + 611
    event_filter._active_system_move_window_id = proxy_window_id
    event_filter._proxy_move_active = True
    event_filter._proxy_move_session_id = session_id

    # OUTOFCONTEXT callbacks can read the *current* generation after old N
    # events were queued. Neither old nor apparently-current tags are allowed
    # to establish START or terminate a layered proxy session.
    event_filter._on_system_move_started(proxy_window_id, session_id - 1)
    event_filter._on_system_move_ended(proxy_window_id, session_id - 1)
    event_filter._on_system_move_started(proxy_window_id, session_id)
    event_filter._on_system_move_ended(proxy_window_id, session_id)

    assert event_filter._system_move_entered is False
    assert event_filter._system_move_exited is False
    assert event_filter._active_system_move_session_id == session_id
    assert event_filter._active_system_move_serial == 611
    assert event_filter._completion_queued_by == ""
    event_filter._proxy_move_active = False
    event_filter._retire_system_move_identity(session_id, 611)


def test_system_move_and_raw_press_tokens_never_wrap_at_old_boundaries() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._system_move_session_counter = 2_000_000_000

    first_session = event_filter._new_system_move_session(701)
    assert first_session == 2_000_000_001
    assert event_filter._retire_system_move_identity(first_session, 701)
    second_session = event_filter._new_system_move_session(702)
    assert second_session == 2_000_000_002

    event_filter._deliverSystemMoveFinished(first_session, 701)
    event_filter._on_system_move_started(4321, first_session)
    event_filter._on_system_move_ended(4321, first_session)
    assert event_filter._active_system_move_session_id == second_session
    assert event_filter._active_system_move_serial == 702
    event_filter._retire_system_move_identity(second_session, 702)

    queued_root = _QueuedNativePressRoot()
    queued_filter = CompactPointerEventFilter(queued_root)
    queued_filter._native_character_request_counter = 1_000_000_000
    assert queued_filter.queueNativeCharacterPress(1.0, 2.0) is True
    assert queued_filter._queued_native_character_request_id == 1_000_000_001
    queued_filter.cancelNativeCharacterPress()


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_native_restore_recovers_from_recreated_root_hwnd(monkeypatch) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    stale_window_id = 2**48 + 74
    event_filter._proxy_root_native_hidden = True
    event_filter._proxy_root_native_window_id = stale_window_id
    native_visibility: list[tuple[int, bool]] = []

    def set_shown(window_id, shown):
        native_visibility.append((int(window_id), bool(shown)))
        # A stale numeric HWND may already be valid for another process-local
        # window, so even a successful raw call is not identity evidence.
        return True

    monkeypatch.setattr(event_filter, "_set_native_window_shown", set_shown)

    assert event_filter._restore_proxy_root_presentation() is True
    assert native_visibility == [(int(root.winId()), True)]
    assert event_filter._proxy_root_native_hidden is False
    assert event_filter._proxy_root_native_window_id == 0
    assert root.update_requests == 1


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_proxy_commit_never_moves_or_shows_a_reused_stale_root_hwnd(
    monkeypatch,
) -> None:
    root = _ProxyMoveRoot()
    old_window_id = 2**48 + 740
    current_window_id = 2**48 + 741
    root.winId = lambda: current_window_id
    event_filter = CompactPointerEventFilter(root)
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=old_window_id
    )
    cache = _ProxyCache()
    cache.final = SimpleNamespace(
        rect=SimpleNamespace(left=150, top=260),
        delta=SimpleNamespace(x=30, y=20),
    )
    event_filter._drag_proxy_cache = cache
    cache.active = True
    event_filter._proxy_move_active = True
    event_filter._proxy_move_session_id = 91
    event_filter._proxy_root_origin_physical = SimpleNamespace(
        left=100, top=200, right=520, bottom=596
    )
    event_filter._proxy_root_native_hidden = True
    event_filter._proxy_root_native_window_id = old_window_id
    moved: list[tuple[int, int, int]] = []
    shown: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_position",
        lambda window_id, x, y: not moved.append(
            (int(window_id), int(x), int(y))
        ),
    )
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_shown",
        lambda window_id, visible: not shown.append(
            (int(window_id), bool(visible))
        ),
    )

    assert event_filter._commit_proxy_geometry(91) is True
    assert moved == [(current_window_id, 130, 220)]
    assert shown == [(current_window_id, True)]
    assert event_filter._native_drag_filter.native_window_id == current_window_id


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_failed_native_restore_retains_recoverable_state(monkeypatch) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    hidden_window_id = 2**48 + 75
    root.winId = lambda: hidden_window_id
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=hidden_window_id
    )
    cache = _ProxyCache()
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_position",
        lambda _window_id, _x, _y: True,
    )
    restore_allowed = False

    def set_shown(_window_id, shown):
        return not bool(shown) or restore_allowed

    monkeypatch.setattr(event_filter, "_set_native_window_shown", set_shown)

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(359) is True
    assert event_filter._commit_proxy_geometry() is True
    assert event_filter._proxy_root_native_hidden is True
    assert event_filter._proxy_root_native_window_id == hidden_window_id
    assert event_filter._proxy_root_restore_timer.isActive() is True
    assert event_filter._proxy_preview_hide_pending is True
    assert event_filter._proxy_preview_hide_timer.isActive() is False
    assert cache.active is True
    event_filter._on_proxy_preview_hide_timeout()
    assert cache.active is True
    assert root.update_requests == 0

    restore_allowed = True
    assert event_filter._restore_proxy_root_presentation() is True
    assert event_filter._proxy_root_native_hidden is False
    assert event_filter._proxy_root_restore_timer.isActive() is False
    assert event_filter._proxy_preview_hide_timer.isActive() is True
    assert root.update_requests == 1
    assert event_filter._complete_proxy_preview() is True
    assert cache.active is False
    event_filter.acknowledgeSystemMoveFinished(359)


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_opacity_is_used_only_when_native_hide_is_unavailable(monkeypatch) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_shown",
        lambda _window_id, _shown: False,
    )

    assert event_filter._hide_proxy_root_presentation(4321) is True
    assert event_filter._proxy_root_native_hidden is False
    assert event_filter._proxy_root_opacity_hidden is True
    assert root.opacity_writes == [0.0]
    assert event_filter._restore_proxy_root_presentation() is True
    assert event_filter._proxy_root_opacity_hidden is False
    assert root.opacity_writes == [0.0, 1.0]


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_native_hide_start_failure_restores_before_direct_frame_fallback(monkeypatch) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    root_window_id = 2**48 + 73
    root.winId = lambda: root_window_id
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=root_window_id
    )
    cache = _ProxyCache(start_move_succeeds=False)
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )
    native_visibility: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_shown",
        lambda window_id, shown: not native_visibility.append(
            (int(window_id), bool(shown))
        ),
    )

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(358) is False
    assert native_visibility == [
        (root_window_id, False),
        (root_window_id, True),
    ]
    assert root.system_move_starts == 0
    assert root.opacity_writes == []
    assert event_filter._proxy_root_native_hidden is False
    assert event_filter._active_system_move_window_id == 0
    assert event_filter.native_system_move_active is False
    event_filter.endDragProxyGesture()
    assert cache.gesture_active is False


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_interrupted_proxy_ack_cancels_native_loop_before_commit(monkeypatch) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    root_window_id = 2**48 + 76
    root.winId = lambda: root_window_id
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=root_window_id
    )
    cache = _ProxyCache()
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_position",
        lambda _window_id, _x, _y: True,
    )
    native_visibility: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_shown",
        lambda window_id, shown: not native_visibility.append(
            (int(window_id), bool(shown))
        ),
    )

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(360) is True
    assert cache.active is True
    root.logical_visible = False

    # This is the route used when presence/privacy changes hide the QML pet
    # while the physical button is still held.
    event_filter.acknowledgeSystemMoveFinished(360)

    assert cache.native_move_cancels == 1
    assert event_filter._proxy_move_active is False
    assert root.opacity_value == 1.0
    assert native_visibility == [(root_window_id, False)]
    assert root.update_requests == 0
    assert cache.active is False
    assert cache.completed == 1
    assert event_filter._proxy_preview_hide_pending is False
    assert cache.gesture_active is False


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_stale_proxy_falls_back_without_starting_unguarded_root_system_move(
    monkeypatch,
) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    root_window_id = 2**48 + 78
    root.winId = lambda: root_window_id
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=root_window_id
    )
    cache = _ProxyCache(prepared=False)
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(362) is False
    assert root.system_move_starts == 0
    assert root.opacity_value == 1.0
    assert event_filter._diagnostic_proxy_used is False
    assert event_filter._proxy_fallback_reason == "stale-key"
    assert event_filter._proxy_runtime_last_mode == "direct-fallback"
    event_filter.endDragProxyGesture()
    assert cache.gesture_active is False


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_hidden_direct_fallback_can_explicitly_release_snapshot_fence(
    monkeypatch,
) -> None:
    root = _ProxyMoveRoot()
    root.startSystemMove = lambda: False
    event_filter = CompactPointerEventFilter(root)
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=2**48 + 82
    )
    cache = _ProxyCache(prepared=False)
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(366) is False
    assert cache.gesture_active is True
    assert event_filter._active_system_move_serial == 0

    event_filter.endDragProxyGesture()
    assert cache.gesture_active is False


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_proxy_preview_hide_from_first_drag_cannot_hide_second_drag(
    monkeypatch,
) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    root_window_id = 2**48 + 79
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=root_window_id
    )
    cache = _ProxyCache()
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )
    monkeypatch.setattr(
        event_filter,
        "_set_native_window_position",
        lambda _window_id, _x, _y: True,
    )

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(363) is True
    assert event_filter._commit_proxy_geometry() is True
    first_generation = event_filter._proxy_preview_hide_generation
    assert first_generation > 0
    assert event_filter._proxy_preview_hide_timer.isSingleShot() is True
    assert event_filter._proxy_preview_hide_timer.isActive() is True
    event_filter.acknowledgeSystemMoveFinished(363)

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(364) is True
    second_generation = event_filter._proxy_session_generation
    assert second_generation > first_generation
    assert cache.completed == 1
    assert cache.active is True
    assert event_filter._proxy_preview_hide_timer.isActive() is False

    # Simulate delivery of the first session's obsolete completion after the
    # second proxy has become active.  It must not touch the new preview.
    assert event_filter._complete_proxy_preview(first_generation) is False
    assert cache.completed == 1
    assert cache.active is True

    assert event_filter._commit_proxy_geometry() is True
    assert event_filter._proxy_preview_hide_generation == second_generation
    assert event_filter._complete_proxy_preview(first_generation) is False
    assert cache.active is True
    assert event_filter._complete_proxy_preview(second_generation) is True
    assert cache.completed == 2
    event_filter.acknowledgeSystemMoveFinished(364)


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows proxy branch")
def test_proxy_start_failure_restores_root_origin_before_direct_fallback(
    monkeypatch,
) -> None:
    root = _ProxyMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    root_window_id = 2**48 + 80
    root.winId = lambda: root_window_id
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=root_window_id
    )
    cache = _ProxyCache(start_move_succeeds=False)
    event_filter._drag_proxy_cache = cache
    monkeypatch.setattr(
        event_filter,
        "_native_window_rect",
        lambda _window_id: SimpleNamespace(
            left=100, top=200, right=520, bottom=596
        ),
    )

    event_filter._reset_drag_diagnostics()
    assert event_filter.tryStartSystemMove(365) is False
    assert root.system_move_starts == 0
    assert root.opacity_value == 1.0
    assert event_filter._active_system_move_window_id == 0
    assert event_filter._system_move_origin_physical == (100, 200)
    assert event_filter._proxy_move_active is False
    assert event_filter._diagnostic_proxy_used is False
    assert event_filter._proxy_fallback_reason == "move-request-failed"
    event_filter.endDragProxyGesture()
    assert cache.gesture_active is False


def test_local_gesture_bridge_reference_counts_filter_ownership() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(app, native_filter)

    assert event_filter.beginLocalGesture("resize") is True
    assert event_filter.beginLocalGesture("resize") is True
    assert event_filter.beginLocalGesture("accessory") is True
    assert app.removed == [native_filter]
    assert event_filter._native_drag_filter_suspended is True
    assert event_filter._qt_pointer_filter_suspended is True
    assert event_filter._local_gesture_total_depth == 3

    assert event_filter.endLocalGesture("resize", False) is True
    assert event_filter.endLocalGesture("accessory", True) is True
    assert app.installed == []
    assert event_filter._local_gesture_total_depth == 1

    assert event_filter.endLocalGesture("resize", False) is True
    assert app.installed == [native_filter]
    assert event_filter._native_drag_filter_suspended is False
    assert event_filter._qt_pointer_filter_suspended is False
    assert event_filter._local_gesture_total_depth == 0


def test_local_gesture_does_not_restore_filters_owned_by_system_move() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(app, native_filter)

    assert event_filter.beginLocalGesture("resize") is True
    assert _start_qt_system_move_compatibility(event_filter, 351) is True
    assert event_filter.endLocalGesture("resize", False) is True
    assert app.installed == []

    event_filter.acknowledgeSystemMoveFinished(351)
    assert app.installed == [native_filter]


def test_system_move_does_not_restore_filters_owned_by_local_gesture() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(app, native_filter)

    assert _start_qt_system_move_compatibility(event_filter, 352) is True
    assert event_filter.beginLocalGesture("action") is True
    event_filter.acknowledgeSystemMoveFinished(352)
    assert app.installed == []

    assert event_filter.endLocalGesture("action", False) is True
    assert app.installed == [native_filter]


def test_local_gesture_rejects_unbounded_surface_names_and_unmatched_end() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)

    assert event_filter.beginLocalGesture("document-title") is False
    assert event_filter.noteLocalGestureRawEvent("resize") is False
    assert event_filter.endLocalGesture("resize", False) is False
    assert event_filter._local_gesture_states == {}
    assert event_filter._local_gesture_total_depth == 0


def test_local_gesture_diagnostic_is_session_bound_and_content_free(tmp_path) -> None:
    QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    report_path = tmp_path / "runtime" / "pet-drag-latest.json"
    event_filter = CompactPointerEventFilter(root, diagnostics_path=report_path)

    assert event_filter.beginLocalGesture("resize") is True
    assert event_filter.noteLocalGestureRawEvent("resize", 120) is True
    assert event_filter.noteLocalGestureSceneCommit("resize", 7) is True
    assert event_filter.noteLocalGestureNativeCommit("resize", 5) is True
    assert event_filter.endLocalGesture("resize", False) is True
    assert event_filter.wait_for_drag_diagnostics_write(1.0) is True

    report = json.loads(report_path.read_text("utf-8"))
    assert report == {
        "applicationVersion": report["applicationVersion"],
        "cancelled": False,
        "durationMs": report["durationMs"],
        "mode": "local",
        "nativeGeometryCommits": 5,
        "processId": os.getpid(),
        "processStartedAt": report["processStartedAt"],
        "rawEvents": 120,
        "recordedAt": report["recordedAt"],
        "runtimeSessionId": report["runtimeSessionId"],
        "sceneCommits": 7,
        "schemaVersion": 4,
        "state": "finished",
        "surface": "resize",
    }
    assert report["durationMs"] >= 0
    assert report["runtimeSessionId"].startswith(f"{os.getpid()}-")
    assert not any(
        key.casefold() in {"x", "y", "cursor", "title", "text"}
        for key in report
    )
    event_filter.close_drag_diagnostics_writer()


def test_local_gesture_release_batches_all_counters_without_per_event_calls(
    tmp_path,
) -> None:
    QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    report_path = tmp_path / "runtime" / "pet-drag-latest.json"
    event_filter = CompactPointerEventFilter(root, diagnostics_path=report_path)

    assert event_filter.beginLocalGesture("action") is True
    assert event_filter.endLocalGestureWithCounts(
        "action",
        False,
        997,
        61,
        19,
    ) is True
    assert event_filter.wait_for_drag_diagnostics_write(1.0) is True

    report = json.loads(report_path.read_text("utf-8"))
    assert report["surface"] == "action"
    assert report["rawEvents"] == 997
    assert report["sceneCommits"] == 61
    assert report["nativeGeometryCommits"] == 19
    assert event_filter.endLocalGestureWithCounts(
        "action",
        False,
        1,
        1,
        1,
    ) is False
    event_filter.close_drag_diagnostics_writer()


def test_local_gesture_close_cancels_diagnostic_and_clears_owners(tmp_path) -> None:
    QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    report_path = tmp_path / "runtime" / "pet-drag-latest.json"
    event_filter = CompactPointerEventFilter(root, diagnostics_path=report_path)
    app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(app, native_filter)

    assert event_filter.beginLocalGesture("accessory") is True
    assert event_filter.noteLocalGestureRawEvent("accessory", 3) is True
    event_filter.close_drag_diagnostics_writer()

    report = json.loads(report_path.read_text("utf-8"))
    assert report["schemaVersion"] == 4
    assert report["surface"] == "accessory"
    assert report["state"] == "cancelled"
    assert report["cancelled"] is True
    assert report["rawEvents"] == 3
    assert event_filter._local_gesture_states == {}
    assert event_filter._local_gesture_depths == {}
    assert event_filter._local_gesture_total_depth == 0
    assert app.removed == [native_filter]
    assert app.installed == [native_filter]
    assert event_filter._native_drag_filter_suspended is False
    assert event_filter._qt_pointer_filter_suspended is False


def test_win_id_change_refreshes_cached_native_filter_target() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    native_app = _NativeFilterApp()

    class NativeFilter:
        native_window_id = 7

    native_filter = NativeFilter()
    event_filter.configure_native_drag_filter(native_app, native_filter)

    assert event_filter.eventFilter(
        root, QEvent(QEvent.Type.WinIdChange)
    ) is False
    assert native_filter.native_window_id == 4321


def test_failed_system_move_restores_python_native_filter_immediately() -> None:
    root = _NativeMoveRoot()
    root.startSystemMove = lambda: False
    event_filter = CompactPointerEventFilter(root)
    app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(app, native_filter)

    assert event_filter.tryStartSystemMove(36) is False
    assert app.removed == [native_filter]
    assert app.installed == [native_filter]
    assert event_filter._native_drag_filter_suspended is False


def test_native_filter_resume_keeps_retry_authority_until_install_succeeds() -> None:
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    app = _NativeFilterApp(failed_install_attempts=1)
    native_filter = object()
    event_filter.configure_native_drag_filter(app, native_filter)

    assert _start_qt_system_move_compatibility(event_filter, 37) is True
    event_filter.acknowledgeSystemMoveFinished(37)
    assert event_filter._native_drag_filter_suspended is True
    assert app.installed == []

    event_filter._resume_native_drag_filter()
    assert event_filter._native_drag_filter_suspended is False
    assert app.installed == [native_filter]


def test_qt_pointer_filter_resume_retries_after_native_filter_succeeds() -> None:
    root = _QtFilterRetryRoot()
    event_filter = CompactPointerEventFilter(root)
    app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(app, native_filter)

    assert _start_qt_system_move_compatibility(event_filter, 371) is True
    event_filter.acknowledgeSystemMoveFinished(371)
    assert event_filter._native_drag_filter_suspended is False
    assert event_filter._qt_pointer_filter_suspended is True
    assert app.installed == [native_filter]

    event_filter._resume_native_drag_filter()
    assert event_filter._qt_pointer_filter_suspended is False
    assert root.qt_installs == [event_filter]


def test_release_watchdog_failure_restores_filter_and_finishes_gesture() -> None:
    app_loop = QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    native_app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(native_app, native_filter)

    assert _start_qt_system_move_compatibility(event_filter, 38) is True

    def unavailable_button_state() -> bool:
        raise OSError("User32 unavailable")

    event_filter._left_button_is_down = unavailable_button_state
    event_filter._poll_system_move_release()
    app_loop.processEvents()

    assert root.completions == [38]
    assert native_app.installed == [native_filter]
    assert event_filter.native_system_move_active is False
    assert event_filter._completion_queued_by == "button-state-unavailable"


def test_release_watchdog_has_bounded_native_move_fail_safe() -> None:
    app_loop = QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    native_app = _NativeFilterApp()
    native_filter = object()
    event_filter.configure_native_drag_filter(native_app, native_filter)

    assert _start_qt_system_move_compatibility(event_filter, 39) is True
    event_filter._system_move_watchdog_started_at = 0.000001
    event_filter._SYSTEM_MOVE_MAX_HOLD_SECONDS = 0.0
    event_filter._poll_system_move_release()
    app_loop.processEvents()

    assert root.completions == [39]
    assert native_app.installed == [native_filter]
    assert event_filter.native_system_move_active is False
    assert event_filter._completion_queued_by == "native-move-timeout"


@pytest.mark.skipif(os.name != "nt", reason="uses Windows native move semantics")
def test_process_move_events_replace_frame_rate_release_polling() -> None:
    app_loop = QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)
    native_window_id = 2**40 + 4321
    event_filter._native_drag_filter = SimpleNamespace(
        native_window_id=native_window_id
    )
    event_filter._system_move_end_watcher = SimpleNamespace(ready=True)

    event_filter._reset_drag_diagnostics()
    event_filter._new_system_move_session(391)
    event_filter._diagnostic_gesture_serial = 391
    event_filter._active_system_move_window_id = native_window_id
    event_filter._system_move_start_returned = True
    event_filter._system_move_release_timer.setSingleShot(False)
    event_filter._system_move_release_timer.setInterval(64)
    event_filter._system_move_release_timer.start()
    assert event_filter._system_move_release_timer.interval() == 64
    assert event_filter._system_move_release_timer.isSingleShot() is False

    event_filter._on_system_move_started(9999)
    assert event_filter._system_move_entered is False
    event_filter._on_system_move_started(native_window_id)
    assert event_filter._system_move_entered is True
    assert event_filter._system_move_release_timer.isSingleShot() is False
    assert event_filter._system_move_release_timer.interval() == 64
    event_filter.noteSystemMoveEntered(100, 100)
    event_filter.noteSystemWindowMoving(112, 100)
    event_filter._system_move_max_distance_squared = 144.0
    event_filter._on_system_move_ended(9999)
    assert event_filter.native_system_move_active is True

    event_filter._on_system_move_ended(native_window_id)
    app_loop.processEvents()

    assert root.completions == [391]
    assert event_filter.native_system_move_active is False
    assert event_filter._system_move_exited is True
    assert event_filter._completion_queued_by == "win-event-move-end"
    assert event_filter._completion_watchdog_polls == 0
    assert event_filter._last_drag_diagnostics["mode"] == "native"
    assert event_filter._last_drag_diagnostics["moved"] is True
    assert (
        event_filter._last_drag_diagnostics["completionQueuedBy"]
        == "win-event-move-end"
    )


def test_system_move_watcher_signal_preserves_pointer_sized_window_ids() -> None:
    QCoreApplication.instance() or QCoreApplication([])
    watcher = _SystemMoveWatcher(0)
    observed: list[int] = []
    large_window_id = 2**48 + 73
    watcher.moveStarted.connect(lambda value: observed.append(int(value)))

    watcher.moveStarted.emit(large_window_id)

    assert observed == [large_window_id]
    assert watcher.close() is True


def test_drag_position_bridge_moves_both_axes_atomically_and_rejects_nan():
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)

    assert event_filter.moveWindowForDrag(-845.5, 731.25) is True
    assert root.positions == [QPoint(-846, 731)]
    assert event_filter.moveWindowForDrag(-845.5, 731.25) is True
    assert root.positions == [QPoint(-846, 731)]
    assert event_filter.moveWindowForDrag(-844.0, 732.0) is True
    assert root.positions == [QPoint(-846, 731), QPoint(-844, 732)]
    assert event_filter.moveWindowForDrag(float("nan"), 0.0) is False
    assert root.positions == [QPoint(-846, 731), QPoint(-844, 732)]


def test_native_move_completion_waits_one_turn_and_honors_release_ack():
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    event_filter = CompactPointerEventFilter(root)

    assert _start_qt_system_move_compatibility(event_filter, 41) is True
    event_filter.queueSystemMoveFinished()
    assert root.completions == []
    app.processEvents()
    assert root.completions == [41]

    assert _start_qt_system_move_compatibility(event_filter, 42) is True
    event_filter.queueSystemMoveFinished()
    event_filter.acknowledgeSystemMoveFinished(42)
    app.processEvents()
    assert root.completions == [41]


def test_drag_diagnostics_are_content_free_and_persist_after_release(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    report_path = tmp_path / "runtime" / "pet-drag-latest.json"
    event_filter = CompactPointerEventFilter(
        root,
        diagnostics_path=report_path,
    )
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(40.0, 50.0),
        QPointF(440.0, 550.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert event_filter.eventFilter(root, press) is False
    assert _start_qt_system_move_compatibility(event_filter, 81) is True
    event_filter.noteSystemMoveEntered(100, 100)
    # The root reports DPR 1.5, so the logical 4 px threshold is 6 physical
    # pixels. The exact boundary is still a click; only a larger path latches.
    event_filter.noteSystemWindowMoving(106, 100)
    assert event_filter.systemMoveHadMotion(81) is False
    event_filter.noteSystemWindowMoving(107, 100)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(46.0, 54.0),
        QPointF(446.0, 554.0),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert event_filter.eventFilter(root, move) is True
    event_filter.noteSystemMoveExited()
    assert event_filter.systemMoveHadMotion(81) is True
    assert event_filter.systemMoveHadMotion(82) is False
    event_filter.completeGestureDiagnostics(81, True, True, True)
    assert event_filter.wait_for_drag_diagnostics_write(1.0) is True

    report = json.loads(report_path.read_text("utf-8"))
    assert report["schemaVersion"] == 4
    assert report["processId"] == os.getpid()
    assert report["runtimeSessionId"].startswith(f"{os.getpid()}-")
    assert report["state"] == "finished"
    assert report["surface"] == "character"
    assert report["mode"] == "native"
    assert report["moved"] is True
    assert report["systemMoveStartReturned"] is True
    assert report["systemMoveEntered"] is True
    assert report["systemMoveExited"] is True
    assert report["systemMovingMessages"] == 2
    assert report["nativeMotionDistancePhysical"] == 7.0
    assert report["dragThresholdPhysical"] == 6.0
    assert report["nativeMouseMovesSuppressed"] == 1
    assert report["directMoveCommits"] == 0
    assert report["devicePixelRatio"] == 1.5
    assert not any(
        key.casefold() in {"x", "y", "cursor", "title", "text"}
        for key in report
    )
    event_filter.close_drag_diagnostics_writer()


def test_drag_diagnostics_keep_armed_phase_in_memory_until_release(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    report_path = tmp_path / "runtime" / "pet-drag-latest.json"
    event_filter = CompactPointerEventFilter(root, diagnostics_path=report_path)

    event_filter._reset_drag_diagnostics()
    assert _start_qt_system_move_compatibility(event_filter, 811) is True
    assert event_filter._diagnostic_phase == "armed"
    assert not report_path.exists()

    event_filter.acknowledgeSystemMoveFinished(811)
    assert event_filter.wait_for_drag_diagnostics_write(1.0) is True
    finished = json.loads(report_path.read_text("utf-8"))
    assert finished["state"] == "finished"
    assert finished["lastNativePhase"] == "armed"
    event_filter.close_drag_diagnostics_writer()


def test_stationary_native_press_still_persists_path_diagnostics(tmp_path):
    """A rejected/no-motion installed path must not be indistinguishable from no use."""

    QCoreApplication.instance() or QCoreApplication([])
    root = _NativeMoveRoot()
    report_path = tmp_path / "runtime" / "pet-drag-latest.json"
    event_filter = CompactPointerEventFilter(
        root,
        diagnostics_path=report_path,
    )

    event_filter._reset_drag_diagnostics()
    assert _start_qt_system_move_compatibility(event_filter, 82) is True
    event_filter.acknowledgeSystemMoveFinished(82)
    assert event_filter.wait_for_drag_diagnostics_write(1.0) is True

    report = json.loads(report_path.read_text("utf-8"))
    assert report["gestureSerial"] == 82
    assert report["mode"] == "native"
    assert report["moved"] is False
    assert report["systemMoveStartReturned"] is True
    assert report["completionQueuedBy"] == "qml-acknowledge"
    event_filter.close_drag_diagnostics_writer()


def test_drag_diagnostics_writer_is_background_single_writer_and_latest_only(
    tmp_path,
):
    writer = _LatestJsonFileWriter(tmp_path / "pet-drag-latest.json")
    main_thread = threading.get_ident()
    first_started = threading.Event()
    release_first = threading.Event()
    writes: list[tuple[int, int]] = []
    original_write = writer._write_snapshot

    def gated_write(snapshot):
        marker = int(snapshot["marker"])
        writes.append((marker, threading.get_ident()))
        if marker == 1:
            first_started.set()
            assert release_first.wait(2.0)
        return original_write(snapshot)

    writer._write_snapshot = gated_write
    writer.submit({"marker": 1})
    assert first_started.wait(1.0)
    writer.submit({"marker": 2})
    latest_sequence = writer.submit({"marker": 3})
    release_first.set()

    assert writer.wait_for_completion(latest_sequence, 2.0)
    assert [marker for marker, _thread in writes] == [1, 3]
    assert all(thread != main_thread for _marker, thread in writes)
    assert json.loads(writer.path.read_text("utf-8")) == {"marker": 3}
    assert writer.close(1.0) is True


def test_drag_diagnostics_writer_preserves_old_file_and_recovers_after_error(
    tmp_path, monkeypatch
):
    path = tmp_path / "pet-drag-latest.json"
    path.write_text('{"marker": 0}\n', "utf-8")
    writer = _LatestJsonFileWriter(path)
    real_replace = os.replace

    def fail_replace(_source, _destination):
        raise PermissionError("diagnostic target is temporarily locked")

    monkeypatch.setattr("lilies.app.os.replace", fail_replace)
    failed_sequence = writer.submit({"marker": 1})
    assert writer.wait_for_completion(failed_sequence, 1.0) is False
    assert json.loads(path.read_text("utf-8")) == {"marker": 0}

    monkeypatch.setattr("lilies.app.os.replace", real_replace)
    recovered_sequence = writer.submit({"marker": 2})
    assert writer.wait_for_completion(recovered_sequence, 1.0) is True
    assert json.loads(path.read_text("utf-8")) == {"marker": 2}
    assert writer.close(1.0) is True


def test_drag_diagnostics_writer_survives_an_unexpected_worker_exception(
    tmp_path,
):
    writer = _LatestJsonFileWriter(tmp_path / "pet-drag-latest.json")
    original_write = writer._write_snapshot
    attempts = 0

    def fail_once(snapshot):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("unexpected serializer failure")
        return original_write(snapshot)

    writer._write_snapshot = fail_once
    failed_sequence = writer.submit({"marker": 1})
    assert writer.wait_for_completion(failed_sequence, 1.0) is False

    recovered_sequence = writer.submit({"marker": 2})
    assert writer.wait_for_completion(recovered_sequence, 1.0) is True
    assert json.loads(writer.path.read_text("utf-8")) == {"marker": 2}
    assert writer.close(1.0) is True
    assert writer.submit({"marker": 3}) == 0


def test_new_press_supersedes_pending_drag_diagnostic_without_disk_wakeup(
    tmp_path,
):
    root = _NativeMoveRoot()
    report_path = tmp_path / "runtime" / "pet-drag-latest.json"
    event_filter = CompactPointerEventFilter(
        root,
        diagnostics_path=report_path,
    )
    event_filter.completeGestureDiagnostics(91, True, False, False)
    assert event_filter._drag_diagnostics_timer.isActive()

    next_press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10.0, 12.0),
        QPointF(110.0, 112.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert event_filter.eventFilter(root, next_press) is False
    assert not event_filter._drag_diagnostics_timer.isActive()
    assert not report_path.exists()

    event_filter.completeGestureDiagnostics(92, False, False, False)
    assert event_filter.wait_for_drag_diagnostics_write(1.0)
    assert json.loads(report_path.read_text("utf-8"))["gestureSerial"] == 92
    event_filter.close_drag_diagnostics_writer()
