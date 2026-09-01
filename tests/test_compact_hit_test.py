from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from lilies.app import CompactHitTestFilter, CompactPointerEventFilter


class _Root:
    def __init__(self) -> None:
        self.values = {
            "compactExpanded": False,
            "compactActionsInteractive": False,
            "compactCharacterLeft": 100,
            "compactCharacterTop": 80,
            "compactCharacterWidth": 120,
            "compactCharacterHeight": 300,
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
    event_filter = CompactPointerEventFilter(root)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(312.0, 118.0),
        QPointF(-845.5, 731.25),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert event_filter.eventFilter(root, event) is False
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

    assert event_filter.tryStartSystemMove(41) is True
    event_filter.queueSystemMoveFinished()
    assert root.completions == []
    app.processEvents()
    assert root.completions == [41]

    assert event_filter.tryStartSystemMove(42) is True
    event_filter.queueSystemMoveFinished()
    event_filter.acknowledgeSystemMoveFinished(42)
    app.processEvents()
    assert root.completions == [41]
