from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMetaObject, QObject, QUrl, Signal
from PySide6.QtQml import QQmlComponent, QQmlEngine

from lilies.app import CompactHitTestFilter, CompactPointerEventFilter
from lilies.drag_proxy_snapshot import DragProxySnapshotCache


class _Root(QObject):
    xChanged = Signal()
    yChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.position = (120.0, 240.0)
        self.native_starts = 0

    def x(self):
        return self.position[0]

    def y(self):
        return self.position[1]

    def width(self):
        return 256

    def height(self):
        return 242

    def devicePixelRatio(self):
        return 1.5

    def screen(self):
        return None

    def winId(self):
        return 4321

    def startSystemMove(self):
        self.native_starts += 1
        return True


class _ObservedHitFilter(CompactHitTestFilter):
    def __init__(self, root):
        self.origin_callbacks = 0
        super().__init__(
            root,
            SimpleNamespace(shell=SimpleNamespace(mode="compact")),
            native_window_id=4321,
        )

    def _refresh_native_window_origin(self, *_args):
        self.origin_callbacks += 1
        super()._refresh_native_window_origin(*_args)


def test_native_motion_disconnects_python_origin_callbacks_until_terminal():
    root = _Root()
    hit_filter = _ObservedHitFilter(root)
    timer = hit_filter._native_hit_refresh_timer
    assert timer is not None and timer.isActive()

    for _ in range(3):
        before = hit_filter.origin_callbacks
        root.xChanged.emit()
        assert hit_filter.origin_callbacks == before + 1
        hit_filter.suspend_drag_observers()
        hit_filter.suspend_drag_observers()
        assert not timer.isActive()
        frozen = hit_filter.origin_callbacks
        root.position = (830.0, 460.0)
        for _ in range(1000):
            root.xChanged.emit()
            root.yChanged.emit()
        # Count Python entry itself, not merely early-returned geometry reads.
        assert hit_filter.origin_callbacks == frozen
        hit_filter.resume_drag_observers()
        hit_filter.resume_drag_observers()
        assert timer.isActive()
        assert hit_filter._native_window_origin == root.position
        after = hit_filter.origin_callbacks
        root.yChanged.emit()
        assert hit_filter.origin_callbacks == after + 1
    timer.stop()


def test_inactive_origin_refresh_timer_is_not_started_by_drag_completion():
    root = _Root()
    hit_filter = _ObservedHitFilter(root)
    hit_filter._native_hit_refresh_timer.stop()
    hit_filter.suspend_drag_observers()
    hit_filter.resume_drag_observers()
    assert not hit_filter._native_hit_refresh_timer.isActive()


def test_previous_resume_cannot_reconnect_observers_into_new_native_owner():
    root = _Root()
    controller = CompactPointerEventFilter(root)
    hit_filter = _ObservedHitFilter(root)
    calls = []
    # Do not configure real Windows hooks or send native messages in this test.
    controller._native_drag_filter = hit_filter
    controller._native_filter_app = SimpleNamespace(
        removeNativeEventFilter=lambda _value: calls.append("remove"),
        installNativeEventFilter=lambda _value: calls.append("install"),
    )
    controller._suspend_native_drag_filter()
    controller._new_system_move_session(41)
    controller._resume_native_drag_filter()
    assert calls == ["remove"]
    assert hit_filter._native_observers_suspended
    frozen = hit_filter.origin_callbacks
    root.xChanged.emit()
    assert hit_filter.origin_callbacks == frozen
    controller.acknowledgeSystemMoveFinished(41)
    assert calls == ["remove", "install"]
    assert not hit_filter._native_observers_suspended
    root.xChanged.emit()
    assert hit_filter._native_window_origin == root.position
    hit_filter._native_hit_refresh_timer.stop()


class _NoImageRead:
    def image(self):
        pytest.fail("held native drag must not convert, scan or upload a frame")


def test_real_root_success_fences_already_ready_snapshot_without_proxy():
    root = _Root()
    controller = CompactPointerEventFilter(root)
    cache = DragProxySnapshotCache(root, root, controller)
    controller._drag_proxy_cache = cache
    cache._grab_generation = 17
    cache._grab_key = "idle-pose"
    cache._grab_pointer = object()
    cache._grab_result = _NoImageRead()

    assert controller.tryStartSystemMove(51)
    assert root.native_starts == 1
    assert not controller.dragProxyActive()
    assert cache._gesture_active
    cache._finish_grab(17, 1.5)
    assert cache._grab_result is None
    assert cache._last_failure == "grab-deferred-for-gesture"
    assert cache._refresh_after_gesture
    controller.acknowledgeSystemMoveFinished(51)
    assert not cache._gesture_active
    cache.close()


@pytest.mark.parametrize("native_attempt", [False, True])
def test_press_fence_survives_stationary_or_refused_move_until_qml_release(
    native_attempt,
):
    root = _Root()
    controller = CompactPointerEventFilter(root)
    cache = DragProxySnapshotCache(root, root, controller)
    controller._drag_proxy_cache = cache
    controller.beginDragProxyGesture()
    controller.beginDragProxyGesture()
    assert cache._gesture_active
    if native_attempt:
        root.startSystemMove = lambda: False
        controller._prepare_proxy_system_move = lambda *_args: False
        assert not controller.tryStartSystemMove(52)
    # No active native identity remains for a stationary/direct-path gesture.
    controller.acknowledgeSystemMoveFinished(52)
    assert cache._gesture_active
    controller.endDragProxyGesture()
    controller.endDragProxyGesture()
    assert not cache._gesture_active
    cache.close()


def _main_qml() -> str:
    return (Path(__file__).resolve().parents[1] / "qml" / "Main.qml").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("held_property", ["manualDragActive", "resizeDragActive"])
def test_posted_layout_timeout_cannot_write_during_new_held_gesture(held_property):
    source = _main_qml()
    marker = "    Timer {\n        id: compactLayoutPersistTimer"
    start = source.index(marker)
    timer_qml = source[start : source.index("\n    Timer {", start + len(marker))]
    # Compile the production Timer unchanged; no window, database or real input.
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(
        ("import QtQuick\nItem {\n"
         "id: desktop\nproperty int writes: 0\n"
         "function persistCompactLayout() { writes += 1 }\n"
         "QtObject { id: petWindow; objectName: 'petWindow'; "
         "property bool manualDragActive: false; "
         "property bool resizeDragActive: false }\n"
         + timer_qml + "\n}").encode("utf-8"),
        QUrl(),
    )
    root = component.create()
    assert root is not None, [error.toString() for error in component.errors()]
    pet = root.findChild(QObject, "petWindow")
    timer = root.findChild(QObject, "compactLayoutPersistTimer")
    assert pet is not None and timer is not None
    try:
        pet.setProperty(held_property, True)
        # A timeout that was already posted may arrive after stop() at re-grab.
        assert QMetaObject.invokeMethod(timer, "triggered")
        assert root.property("writes") == 0
        pet.setProperty(held_property, False)
        assert QMetaObject.invokeMethod(timer, "triggered")
        assert root.property("writes") == 1
    finally:
        root.deleteLater()
        engine.collectGarbage()


def test_character_press_cancels_pending_save_and_terminal_thaws_snapshot():
    source = _main_qml()
    press = source.split("function prepareCharacterGestureAtGlobal(", 1)[1]
    press = press.split("function ", 1)[0]
    assert press.index("compactLayoutPersistTimer.stop()") < press.index(
        "cancelPositionAnimations()"
    )
    assert press.index("beginDragProxyGesture()") < press.index(
        "cancelPositionAnimations()"
    )
    finish = source.split("function finishCharacterGesture(", 1)[1]
    finish = finish.split("function finalizeMovedCharacterGesture", 1)[0]
    assert finish.index("endDragProxyGesture()") < finish.index(
        "Qt.callLater(scheduleDragProxySnapshot)"
    )
    assert "desktop.scheduleCompactLayoutPersistence()" in finish
