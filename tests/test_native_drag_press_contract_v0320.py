from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_native_system_move_is_requested_from_character_press() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    press_start = source.index("onCharacterPressStarted:")
    move_start = source.index("onCharacterPointerMoved:", press_start)
    press_handler = source[press_start:move_start]

    assert "petWindow.tryNativeSystemMove()" in press_handler
    assert press_handler.index("petWindow.dragStartCursorY = cursor.y") < press_handler.index(
        "petWindow.tryNativeSystemMove()"
    )
    assert "petWindow.nativeSystemMoveStartPending = false" in press_handler


def test_direct_event_time_drag_is_the_default_and_system_move_is_opt_in() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    start = source.index("function tryNativeSystemMove()")
    end = source.index("function finishCharacterGesture(", start)
    handler = source[start:end]

    assert 'if (backend.petDragMode !== "system")\n                return false' in handler
    assert handler.index('backend.petDragMode !== "system"') < handler.index(
        "nativeMoveController.tryStartSystemMove("
    )
    assert 'model: ["直接跟手（流畅）", "Windows 系统拖动"]' in source
    assert 'currentIndex: backend.petDragMode === "system" ? 1 : 0' in source
    assert '["direct", "system"][currentIndex]' in source


def test_qml_uses_boolean_preserving_python_system_move_bridge() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    start = source.index("function tryNativeSystemMove()")
    end = source.index("function followPointerAt(", start)
    handler = source[start:end]

    assert "nativeMoveController.tryStartSystemMove(" in handler
    assert "nativeSystemMoveGestureSerial" in handler
    assert "Boolean(startSystemMove())" not in handler
    assert handler.index("nativeSystemMoveStartPending = true") < handler.index(
        "nativeMoveController.tryStartSystemMove("
    )
    assert handler.index("nativeMoveController.tryStartSystemMove(") < handler.index(
        "nativeSystemMoveStartPending = false"
    )

    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    assert "def tryStartSystemMove(self, gesture_serial: int) -> bool:" in app_source
    assert "started = bool(self.root.startSystemMove())" in app_source
    assert 'pet_window.setProperty("nativeMoveController", pointer_event_filter)' in app_source


def test_native_system_move_and_manual_fallback_never_move_same_frame() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    follow_start = source.index("function followPointerAt(")
    consume_start = source.index("function consumePointerEvent(", follow_start)
    follow_handler = source[follow_start:consume_start]

    native_guard = follow_handler.index("if (nativeSystemMoveActive)\n                    return")
    manual_assignment = follow_handler.index(
        "moveWindowForDrag(cursorX - dragGrabOffsetX"
    )
    assert native_guard < manual_assignment


def test_native_move_latches_path_displacement_even_if_it_returns_to_origin() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    start = source.index("function recordNativeWindowMotion()")
    end = source.index("onVisibleChanged:", start)
    handler = source[start:end]

    assert "onXChanged: recordNativeWindowMotion()" in source
    assert "onYChanged: recordNativeWindowMotion()" in source
    assert "nativeSystemMoveStartPending" in handler
    assert "nativeSystemMoveActive" in handler
    assert "x - dragWindowX, y - dragWindowY" in handler
    assert "dragMoved = true" in handler


def test_native_move_completion_is_serialized_and_cancel_waits_for_it() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    finish_start = source.index("function finishNativeSystemMove(")
    finish_end = source.index("function followPointerAt(", finish_start)
    finish_handler = source[finish_start:finish_end]
    cancel_start = source.index("onCharacterCanceled:")
    cancel_end = source.index("onWheelStepped:", cancel_start)
    cancel_handler = source[cancel_start:cancel_end]

    assert "serial !== nativeSystemMoveGestureSerial" in finish_handler
    assert "finishCharacterGesture(false, true, serial)" in finish_handler
    assert "if (petWindow.nativeSystemMoveStartPending" in cancel_handler
    assert "|| petWindow.nativeSystemMoveActive" in cancel_handler
    assert "petWindow.nativeSystemMoveCancelPending = true" in cancel_handler
    assert cancel_handler.index("nativeSystemMoveCancelPending = true") < cancel_handler.index(
        "return"
    )

    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    assert "WM_EXITSIZEMOVE = 0x0232" in app_source
    assert "controller.queueSystemMoveFinished()" in app_source
    assert "QTimer.singleShot(" in app_source
    assert "_deliverSystemMoveFinished(serial)" in app_source


def test_gesture_finish_has_one_stationary_and_one_moved_outcome() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    start = source.index("function finishCharacterGesture(")
    end = source.index("function finishNativeSystemMove(", start)
    handler = source[start:end]

    assert "detachForManualDrag()" in handler
    assert "desktop.clampDraggedFigureToArea(targetArea, true)" in handler
    assert handler.index("desktop.clampDraggedFigureToArea(targetArea, true)") < handler.index(
        "detachForManualDrag()"
    )
    assert "dragPresentationSettleTimer.restart()" in handler
    assert "compactWindow.expanded = !compactWindow.expanded" in handler
    assert "nativeMoveController.acknowledgeSystemMoveFinished(" in handler
    assert "desktop.scheduleCompactLayoutPersistence()" in handler


def test_direct_drag_detaches_before_first_pointer_reposition() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    start = source.index("function followPointerAt(")
    end = source.index("function consumePointerEvent(", start)
    handler = source[start:end]

    first_drag = handler.index("if (!dragMoved) {")
    detach = handler.index("detachForManualDrag()", first_drag)
    first_position = handler.index(
        "moveWindowForDrag(cursorX - dragGrabOffsetX", first_drag
    )
    assert first_drag < detach < first_position

    body_source = (ROOT / "qml" / "V03PetBody.qml").read_text("utf-8")
    assert "property bool interactionSnap: false" in body_source
    assert body_source.count("enabled: !root.interactionSnap") >= 20
    assert "onManualDragActiveChanged:" in source
    assert "compactLilith.interactionSnap = false" in source


def test_release_clamp_tracks_visible_figure_and_rechecks_after_pose_transition() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    clamp_start = source.index("function clampDraggedFigureToArea(")
    clamp_end = source.index("function constrainCompactPet(", clamp_start)
    clamp_handler = source[clamp_start:clamp_end]

    assert "compactLilith.figureLeft" in clamp_handler
    assert "compactLilith.figureTop" in clamp_handler
    assert "compactLilith.figureWidth" in clamp_handler
    assert "compactLilith.figureHeight" in clamp_handler
    assert "figureWidth - 56" in clamp_handler
    assert "figureHeight - 72" in clamp_handler

    timer_start = source.index("id: dragPresentationSettleTimer")
    timer_end = source.index("function applyHabitatState()", timer_start)
    timer = source[timer_start:timer_end]
    assert "interval: 340" in timer
    assert "petWindow.manualDragActive || backend.habitatState.attached" in timer
    assert "desktop.clampDraggedFigureToArea(area, true)" in timer
