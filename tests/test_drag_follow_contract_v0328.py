from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _main_source() -> str:
    return (ROOT / "qml" / "Main.qml").read_text("utf-8")


def test_direct_character_drag_uses_the_atomic_window_position_bridge() -> None:
    source = _main_source()
    helper_start = source.index("function moveWindowForDrag(targetX, targetY)")
    helper_end = source.index("function tryNativeSystemMove(", helper_start)
    helper = source[helper_start:helper_end]
    follow_start = source.index("function followPointerAt(")
    follow_end = source.index("function consumePointerEvent(", follow_start)
    follow = source[follow_start:follow_end]

    assert "nativeMoveController.moveWindowForDrag(" in helper
    assert "x = targetX" in helper and "y = targetY" in helper
    # One frame has one geometry authority and one atomic commit.  The old
    # first-threshold branch submitted an eager move and then submitted the
    # same target again at the bottom of this function.
    assert follow.count("moveWindowForDrag(") == 1
    assert follow.index("nativeSystemMoveStartPending") < follow.index(
        "moveWindowForDrag(targetX, targetY)"
    )

    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    assert "def moveWindowForDrag(self, x: float, y: float) -> bool:" in app_source
    assert "target = QPoint(" in app_source
    assert "self.root.setPosition(target)" in app_source


def test_high_rate_pointer_events_are_coalesced_to_one_move_per_drag_frame() -> None:
    source = _main_source()
    event_start = source.index("function followPointerEvent(")
    event_end = source.index("function followPendingPointerEvent(", event_start)
    event_handler = source[event_start:event_end]
    frame_start = source.index("function followPointerFrame()")
    frame_end = source.index("function followGlobalPointerNow()", frame_start)
    frame_handler = source[frame_start:frame_end]

    assert "dragPointerEventPending = true" in event_handler
    assert "followPointerAt(" not in event_handler
    assert "if (followPendingPointerEventInternal(true))" in frame_handler
    assert "followCapturedPointerEventInternal(true)" in frame_handler
    assert "nativeSystemMoveStartPending" in frame_handler
    assert "nativeSystemMoveActive" in frame_handler
    frame_animation_start = source.index("FrameAnimation {", frame_end)
    frame_animation_end = source.index("Timer {", frame_animation_start)
    frame_animation = source[frame_animation_start:frame_animation_end]
    assert "running: petWindow.manualDragActive" in frame_animation
    assert "&& !petWindow.nativeSystemMoveStartPending" in frame_animation
    assert "onTriggered: petWindow.followPointerFrame()" in frame_animation
    assert "interval:" not in frame_animation

    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    event_filter_start = app_source.index("def eventFilter(", app_source.index("class CompactPointerEventFilter"))
    event_filter_end = app_source.index("class QuickWindowResourceLifecycle", event_filter_start)
    event_filter = app_source[event_filter_start:event_filter_end]
    assert "self._latest_global_x" in event_filter
    assert 'self.root.setProperty("capturedPointerGlobalX"' not in event_filter
    assert "def takeLatestPointerEvent(" in app_source


def test_prestart_flick_is_coalesced_and_release_never_starts_native_move() -> None:
    source = _main_source()
    prime_start = source.index("function primeQueuedNativeCharacterPress(")
    prime_end = source.index("function finishQueuedNativeCharacterPress(", prime_start)
    prime = source[prime_start:prime_end]
    finish_start = source.index("function finishCharacterGesture(")
    finish_end = source.index("function finalizeMovedCharacterGesture(", finish_start)
    finish = source[finish_start:finish_end]

    assert "followCapturedPointerEventInternal(false)" in prime
    assert "followPendingPointerEventInternal(false)" in finish
    assert "followCapturedPointerEventInternal(false)" in finish
    assert "followGlobalPointerSample(true, false)" in finish

    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    assert "def recordQueuedNativeCharacterPointer(" in app_source
    assert "self._queued_native_character_prestart_samples + 1" in app_source
    assert '"releaseBeforeQueuedStart"' in app_source
    assert '"prestartMaxDistanceLogical"' in app_source
    assert '"prestartMaxDistancePhysical"' in app_source
    assert '"prestartMotionLatched"' in app_source
    assert '"prestartScreenMapMiss"' in app_source
    assert "function latchQueuedNativeCharacterMotion(" in source


def test_native_move_is_consumed_after_press_without_swallowing_release() -> None:
    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    event_filter_start = app_source.index(
        "def eventFilter(", app_source.index("class CompactPointerEventFilter")
    )
    event_filter_end = app_source.index(
        "class QuickWindowResourceLifecycle", event_filter_start
    )
    event_filter = app_source[event_filter_start:event_filter_end]

    assert event_filter.index("self._active_system_move_serial > 0") < event_filter.index(
        "point = event.globalPosition()"
    )
    assert "event.type() == QEvent.Type.MouseMove" in event_filter
    assert 'bool(self.root.property("manualDragActive"))' in event_filter
    assert "MouseButtonRelease" not in event_filter.split("return (", 1)[1]


def test_drag_freezes_action_and_desktop_tab_screen_placement_bindings() -> None:
    source = _main_source()
    safe_start = source.index("function safeActionX(")
    safe_end = source.index("function actionGridColumn(", safe_start)
    safe_helpers = source[safe_start:safe_end]
    tab_start = source.index("id: desktopModeTab")
    tab_end = source.index("MouseArea {", tab_start)
    desktop_tab = source[tab_start:tab_end]

    assert "petWindow.presentationWindowX" in safe_helpers
    assert "petWindow.presentationWindowY" in safe_helpers
    assert "petWindow.x" not in safe_helpers
    assert "petWindow.y" not in safe_helpers
    assert "petWindow.presentationWindowX" in desktop_tab


def test_drag_reuses_work_area_and_pauses_unrelated_geometry_heartbeat() -> None:
    source = _main_source()
    follow_start = source.index("function followPointerAt(")
    follow_end = source.index("function consumePointerEvent(", follow_start)
    follow = source[follow_start:follow_end]
    heartbeat_start = source.index("// The figure/window bounds are stable")
    heartbeat_end = source.index("Rectangle {", heartbeat_start)
    heartbeat = source[heartbeat_start:heartbeat_end]

    assert "var targetArea = dragWorkAreaAt(cursorX, cursorY)" in follow
    assert "function presentationWorkArea()" in source
    assert "!petWindow.manualDragActive" in heartbeat
    assert "!petWindow.resizeDragActive" in heartbeat


def test_box_and_resize_handlers_share_the_four_pixel_activation_contract() -> None:
    source = _main_source()
    for handler_id in ("petResizeDrag", "componentMoveDrag", "accessoryDrag"):
        start = source.index(f"id: {handler_id}")
        end = source.index("onActiveChanged:", start)
        assert "dragThreshold: 4" in source[start:end]


def test_resize_tracks_the_global_pointer_and_held_handle_across_screens() -> None:
    source = _main_source()
    start = source.index("id: petResizeDrag")
    end = source.index("WheelHandler {", start)
    handler = source[start:end]

    assert "centroid.scenePressPosition.x" in handler
    assert "centroid.scenePosition.x - startCursorX" in handler
    assert "centroid.scenePosition.y - startCursorY" in handler
    assert "backend.cursorPosition()" not in handler
    assert "var cursorX = startCursorX + pointerDx" in handler
    assert "var cursorY = startCursorY + pointerDy" in handler
    assert "petWindow.dragWorkArea = desktop.workAreaAt(" in handler
    assert "petWindow.dragWorkAreaAt(" in handler
    assert "startFigureRight" in handler and "startFigureBottom" in handler
    assert "var sizeRatio = desktop.compactBoxSize" in handler
    assert "desiredHandleX - nextHandleLocalX" in handler
    assert "desiredHandleY - nextHandleLocalY" in handler
    assert "desktop.resizeCompactPetForDrag(" in handler
    assert "desired - desktop.compactBoxSize, false, false," in handler
    assert "cursorX, cursorY" in handler

    resize_start = source.index("function resizeCompactPetForDrag(")
    resize_end = source.index("function openWorkPanel(", resize_start)
    resize = source[resize_start:resize_end]
    assert "interactionGlobalX" in resize and "interactionGlobalY" in resize
    assert "? petWindow.dragWorkAreaAt(areaX, areaY)" in resize
    assert ": workAreaAt(areaX, areaY)" in resize
    assert "clampDuringResize === undefined" in resize


def test_local_drag_events_only_stage_the_latest_translation_for_a_frame() -> None:
    source = _main_source()
    forbidden_commits = {
        "petResizeDrag": (
            "backend.cursorPosition()",
            "desktop.resizeCompactPetForDrag(",
            "petWindow.moveWindowForDrag(",
        ),
        "componentMoveDrag": (
            "componentButton.offsetX =",
            "componentButton.offsetY =",
            "backend.saveComponentLayout(",
        ),
        "accessoryDrag": (
            "compactWindow.accessoryDx =",
            "compactWindow.accessoryDy =",
            "backend.saveAccessoryBoxLayout(",
        ),
    }

    for handler_id, forbidden in forbidden_commits.items():
        start = source.index(f"id: {handler_id}")
        end = source.index("WheelHandler {", start)
        section = source[start:end]
        raw_start = section.index("onTranslationChanged:")
        raw_end = section.index("}", raw_start)
        raw_handler = section[raw_start:raw_end]

        if handler_id == "petResizeDrag":
            assert "centroid.scenePosition.x - startCursorX" in raw_handler
            assert "centroid.scenePosition.y - startCursorY" in raw_handler
        else:
            assert "latestTranslationX = Number(translation.x)" in raw_handler
            assert "latestTranslationY = Number(translation.y)" in raw_handler
        assert "translationPending = true" in raw_handler
        for commit in forbidden:
            assert commit not in raw_handler

        frame_start = section.index("FrameAnimation {", raw_end)
        frame = section[frame_start:]
        assert f"{handler_id}.translationPending" in frame
        assert f"{handler_id}.flushPendingTranslation()" in frame


def test_local_drag_release_flushes_before_persistence_and_unlock() -> None:
    source = _main_source()
    finalizers = {
        "petResizeDrag": "desktop.persistCompactLayout()",
        "componentMoveDrag": "backend.saveComponentLayout(",
        "accessoryDrag": "backend.saveAccessoryBoxLayout(",
    }

    for handler_id, finalizer in finalizers.items():
        start = source.index(f"id: {handler_id}")
        active_start = source.index("onActiveChanged:", start)
        active_end = source.index("onTranslationChanged:", active_start)
        active_handler = source[active_start:active_end]

        assert active_handler.index("flushPendingTranslation()") < active_handler.index(
            finalizer
        )
        assert active_handler.index("flushPendingTranslation()") < active_handler.rindex(
            "setPetInteractionLock("
        )


def test_grid_packed_actions_do_not_accept_or_save_right_drag_layouts() -> None:
    source = _main_source()
    start = source.index("id: componentMoveDrag")
    end = source.index("WheelHandler {", start)
    handler = source[start:end]
    active_start = handler.index("onActiveChanged:")
    active_end = handler.index("onTranslationChanged:", active_start)
    active_handler = handler[active_start:active_end]

    assert "enabled: !compactWindow.actionGridMode" in handler
    guard = active_handler.index("if (!compactWindow.actionGridMode)")
    save = active_handler.index("backend.saveComponentLayout(")
    assert guard < save


def test_accessory_box_uses_the_visible_window_bounds_not_fixed_early_stops() -> None:
    source = _main_source()
    start = source.index("id: accessoryDrag")
    end = source.index("WheelHandler {", start)
    handler = source[start:end]

    assert "compactWindow.width" in handler
    assert "compactWindow.height" in handler
    assert "minimumDx" in handler and "maximumDx" in handler
    assert "minimumDy" in handler and "maximumDy" in handler
    assert "Math.min(1.20" not in handler


def test_resize_and_character_drag_freeze_local_motion_while_held() -> None:
    source = _main_source()
    pet_start = source.index("V03PetBody {")
    pet_end = source.index("Timer {", pet_start)
    pet = source[pet_start:pet_end]

    assert "paused: petWindow.manualDragActive" in pet
    assert "|| petWindow.resizeDragActive" in pet
    apply_start = source.index("function applyHabitatState()")
    apply_end = source.index("Connections {", apply_start)
    assert "manualDragActive || petWindow.resizeDragActive" in source[
        apply_start:apply_end
    ]
