from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_native_system_move_is_requested_from_character_press() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    press_start = source.index("onCharacterPressStarted:")
    move_start = source.index("onCharacterPointerMoved:", press_start)
    press_handler = source[press_start:move_start]

    assert "petWindow.tryNativeSystemMove(" in press_handler
    assert "petWindow.dragLatchedSnapshotKey" in press_handler
    assert "petWindow.dragLatchedGeometryKey" in press_handler
    assert press_handler.index("petWindow.dragStartCursorY = cursor.y") < press_handler.index(
        "petWindow.tryNativeSystemMove("
    )
    assert press_handler.index("petWindow.dragLatchedSnapshotKey =") < press_handler.index(
        "petWindow.tryNativeSystemMove("
    )
    assert press_handler.index("petWindow.dragLatchedGeometryKey =") < press_handler.index(
        "petWindow.tryNativeSystemMove("
    )
    assert "petWindow.nativeSystemMoveStartPending = false" in press_handler


def test_system_move_is_the_recommended_default_and_direct_is_compatibility_mode() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    start = source.index("function tryNativeSystemMove(")
    end = source.index("function finishCharacterGesture(", start)
    handler = source[start:end]

    assert 'if (backend.petDragMode !== "system")\n                return false' in handler
    assert handler.index('backend.petDragMode !== "system"') < handler.index(
        "nativeMoveController.tryStartSystemMove("
    )
    assert 'model: ["Windows 原生拖动（推荐）", "直接跟手（兼容）"]' in source
    assert 'currentIndex: backend.petDragMode === "system" ? 0 : 1' in source
    assert '["system", "direct"][currentIndex]' in source
    assert "Qt.NoDropShadowWindowHint" in source


def test_drag_hides_auxiliary_animated_quick_windows_without_cancelling_them() -> None:
    main_source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    bubble_source = (ROOT / "qml" / "CompanionBubble.qml").read_text("utf-8")
    focus_source = (ROOT / "qml" / "FocusDiversionBubble.qml").read_text("utf-8")

    assert "property bool interactionHidden: false" in bubble_source
    assert "&& !interactionHidden" in bubble_source
    assert (
        "running: bubbleWindow.visible\n"
        "                                 && bubbleWindow.effectiveBusy"
    ) in bubble_source
    assert "else if (!interactionHidden)" in bubble_source
    assert "property bool interactionHidden: false" in focus_source
    assert "interactionHidden: petWindow.manualDragActive" in main_source
    assert "&& !petWindow.manualDragActive" in main_source
    assert "running: selectionBubble.visible" in main_source


def test_qml_uses_boolean_preserving_python_system_move_bridge() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    start = source.index("function tryNativeSystemMove(")
    end = source.index("function followPointerAt(", start)
    handler = source[start:end]

    assert "nativeMoveController.tryStartSystemMove(" in handler
    assert "nativeSystemMoveGestureSerial" in handler
    assert "requestedSnapshotKey" in handler
    assert "requestedGeometryKey" in handler
    assert "requestedSerial," in handler
    assert "requestedSnapshotKey," in handler
    assert "requestedGeometryKey))" in handler
    assert "nativeMoveController.dragProxyActive()" in handler
    assert "Boolean(startSystemMove())" not in handler
    assert handler.index("nativeSystemMoveStartPending = true") < handler.index(
        "nativeMoveController.tryStartSystemMove("
    )
    assert handler.index("nativeMoveController.tryStartSystemMove(") < handler.index(
        "nativeSystemMoveStartPending = false"
    )

    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    assert "gesture_serial: int," in app_source
    assert "semantic_key: str = \"\"," in app_source
    assert "geometry_key: str = \"\"," in app_source
    assert "self._prepare_proxy_system_move(" in app_source
    assert "str(semantic_key or \"\")" in app_source
    assert "str(geometry_key or \"\")" in app_source
    assert "started = bool(self.root.startSystemMove())" in app_source
    assert 'pet_window.setProperty("nativeMoveController", pointer_event_filter)' in app_source


def test_native_system_move_and_manual_fallback_never_move_same_frame() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    follow_start = source.index("function followPointerAt(")
    consume_start = source.index("function consumePointerEvent(", follow_start)
    follow_handler = source[follow_start:consume_start]

    native_guard = follow_handler.index(
        "if (!manualDragActive || nativeSystemMoveStartPending"
    )
    manual_assignment = follow_handler.index("moveWindowForDrag(targetX, targetY)")
    assert native_guard < manual_assignment
    assert follow_handler.count("moveWindowForDrag(") == 1
    frame_start = source.index("function followPointerFrame()")
    frame_end = source.index("function followGlobalPointerNow()", frame_start)
    frame_handler = source[frame_start:frame_end]
    assert "nativeSystemMoveStartPending" in frame_handler
    assert "nativeSystemMoveActive" in frame_handler


def test_native_move_latches_path_without_qml_geometry_hot_callbacks() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    assert "onXChanged: recordNativeWindowMotion()" not in source
    assert "onYChanged: recordNativeWindowMotion()" not in source
    assert "function recordNativeWindowMotion()" not in source

    start = source.index("function finishCharacterGesture(")
    end = source.index("function finishNativeSystemMove(", start)
    handler = source[start:end]
    assert "nativeMoveController.systemMoveHadMotion(" in handler
    assert "dragMoved || nativePathMoved" in handler

    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    assert "def systemMoveHadMotion(" in app_source
    assert "self._system_move_max_distance_squared" in app_source
    assert "self._system_move_threshold_physical**2" in app_source


def test_native_move_completion_is_serialized_and_cancel_waits_for_it() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    finish_start = source.index("function finishNativeSystemMove(")
    finish_end = source.index("function followPointerAt(", finish_start)
    finish_handler = source[finish_start:finish_end]
    cancel_start = source.index("onCharacterCanceled:")
    cancel_end = source.index("onWheelStepped:", cancel_start)
    cancel_handler = source[cancel_start:cancel_end]
    release_start = source.index("onCharacterReleased:")
    release_end = source.index("onCharacterCanceled:", release_start)
    release_handler = source[release_start:release_end]

    assert "serial !== nativeSystemMoveGestureSerial" in finish_handler
    assert "finishCharacterGesture(false, true, serial)" in finish_handler
    assert "if (petWindow.nativeSystemMoveStartPending" in cancel_handler
    assert "|| petWindow.nativeSystemMoveActive" in cancel_handler
    assert "petWindow.nativeSystemMoveCancelPending = true" in cancel_handler
    assert cancel_handler.index("nativeSystemMoveCancelPending = true") < cancel_handler.index(
        "return"
    )
    assert "petWindow.nativeSystemMoveStartPending" in release_handler
    assert "|| petWindow.nativeSystemMoveActive" in release_handler
    assert release_handler.index("nativeSystemMoveCancelPending = true") < release_handler.index(
        "return"
    )
    assert release_handler.index("return") < release_handler.index(
        "petWindow.finishCharacterGesture("
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

    assert "dragFinalizePending = true" in handler
    assert "desktop.clampDraggedFigureToArea(targetArea, true)" in handler
    assert handler.index("desktop.clampDraggedFigureToArea(targetArea, true)") < handler.index(
        "dragFinalizePending = true"
    )
    finalizer_start = handler.index("function finalizeMovedCharacterGesture()")
    finalizer = handler[finalizer_start:]
    assert "backend.detachPetHabitat(x, y)" in finalizer
    assert "dragPresentationSettleTimer.restart()" in finalizer
    assert handler.index("Qt.callLater(function()") < finalizer_start
    assert "compactWindow.expanded = !dragMenuWasExpanded" in handler
    assert "nativeMoveController.acknowledgeSystemMoveFinished(" in handler
    assert "desktop.scheduleCompactLayoutPersistence()" in handler


def test_drag_freezes_pose_and_defers_detach_until_after_release() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    start = source.index("function followPointerAt(")
    end = source.index("function consumePointerEvent(", start)
    handler = source[start:end]

    first_drag = handler.index("if (!dragMoved) {")
    first_position = handler.index("moveWindowForDrag(targetX, targetY)", first_drag)
    assert first_drag < first_position
    assert "detachForManualDrag()" not in handler
    assert "remapCharacterGrabAfterDetach()" not in handler

    assert "function recordNativeWindowMotion()" not in source

    press_start = source.index("onCharacterPressStarted:")
    press_end = source.index("onCharacterPointerMoved:", press_start)
    press_handler = source[press_start:press_end]
    assert "compactWindow.prepareForCharacterDrag()" in press_handler
    assert "petWindow.dragLatchedSnapshotKey" in press_handler
    assert "petWindow.dragLatchedGeometryKey" in press_handler
    assert "var nativeStarted = petWindow.tryNativeSystemMove(" in press_handler
    fallback_guard = press_handler.index(
        "if (!nativeStarted || !petWindow.nativeSystemMoveUsesProxy)"
    )
    interaction_snap = press_handler.index("compactLilith.interactionSnap = true")
    assert fallback_guard < interaction_snap
    assert press_handler.count("compactLilith.interactionSnap = true") == 1
    assert "dragInteractionLockTimer.restart()" in press_handler
    timer_start = source.index("id: dragInteractionLockTimer")
    timer_end = source.index("id: dragProxySnapshotDebounce", timer_start)
    interaction_timer = source[timer_start:timer_end]
    assert "interval: 40" in interaction_timer
    assert 'backend.setPetInteractionLock("character", true)' in interaction_timer
    assert "orbitProgressAnimation.stop()" in source
    assert "boxRotationAnimation.stop()" in source
    assert source.count("enabled: !petWindow.manualDragActive") >= 2

    body_source = (ROOT / "qml" / "V03PetBody.qml").read_text("utf-8")
    assert "property bool interactionSnap: false" in body_source
    assert body_source.count("enabled: !root.interactionSnap") >= 20
    assert "onManualDragActiveChanged:" in source
    assert "compactLilith.interactionSnap = false" in source


def test_pending_release_detach_is_committed_before_hidden_state_clears_it() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    pet_start = source.index("id: petWindow")
    visible_start = source.index("onVisibleChanged:", pet_start)
    visible_end = source.index("Behavior on x", visible_start)
    handler = source[visible_start:visible_end]

    assert handler.index("if (dragFinalizePending)") < handler.index(
        "finalizeInterruptedInteractionForHide()"
    )
    assert handler.index("finalizeMovedCharacterGesture()") < handler.index(
        "dragFinalizePending = false"
    )
    assert "nativeMoveController.endDragProxyGesture()" in handler


def test_character_drag_uses_one_forgiving_shared_interaction_mask() -> None:
    main_source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    body_source = (ROOT / "qml" / "V03PetBody.qml").read_text("utf-8")

    assert (
        "return compactLilith.containsCharacterInteractionPoint(localX, localY)"
        in main_source
    )
    assert "function containsCharacterInteractionPoint(rootX, rootY)" in body_source
    assert "readonly property real characterHitTolerance: 6.0" in body_source
    assert "var tolerance = characterHitTolerance" in body_source
    assert (
        "return root.containsCharacterInteractionPoint(point.x, point.y)"
        in body_source
    )
    # Keep the exact visual mask available to asset/geometry verifiers and
    # ensure the interaction dilation delegates to it instead of accepting the
    # whole transparent QQuickWindow rectangle.
    assert "function containsCharacterPoint(rootX, rootY)" in body_source
    interaction_start = body_source.index(
        "function containsCharacterInteractionPoint(rootX, rootY)"
    )
    interaction_end = body_source.index(
        "function normalizedCharacterGrab(rootX, rootY)", interaction_start
    )
    interaction = body_source[interaction_start:interaction_end]
    assert interaction.count("containsCharacterPoint(") >= 2
    assert "return true" not in interaction.split("containsCharacterPoint", 1)[0]


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
