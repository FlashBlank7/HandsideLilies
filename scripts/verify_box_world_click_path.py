from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QPA_OFFSCREEN_SIZE", "1200x900")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.app import CompactHitTestFilter, CompactPointerEventFilter
from lilies.backend import Backend
from lilies.paths import qml_path


def _item_center(item: QQuickItem) -> QPoint:
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(point.x()), round(point.y()))


def _named_item(window: QQuickWindow, object_name: str) -> QQuickItem | None:
    return next(
        (
            item
            for item in CompactHitTestFilter._visual_descendants(window.contentItem())
            if item.objectName() == object_name
        ),
        None,
    )


def _action(pet_window: QQuickWindow, action_id: str) -> QQuickItem | None:
    return _named_item(pet_window, f"desktopPetAction_{action_id}")


def _character_point(pet_window: QQuickWindow) -> QPoint:
    return QPoint(
        round(
            float(pet_window.property("compactCharacterLeft"))
            + float(pet_window.property("compactCharacterWidth")) * 0.50
        ),
        round(
            float(pet_window.property("compactCharacterTop"))
            + float(pet_window.property("compactCharacterHeight")) * 0.45
        ),
    )


def _window_name(window: QWindow | None) -> str:
    if window is None:
        return ""
    return str(window.objectName() or window.title() or type(window).__name__)


def _pump_quick_frame(window: QQuickWindow, wait_ms: int = 0) -> None:
    """Commit one deterministic frame when the offscreen QPA coalesces it."""

    window.requestUpdate()
    if wait_ms > 0:
        QTest.qWait(wait_ms)
    QApplication.processEvents()
    window.grabWindow()
    QApplication.processEvents()


def _settle_pet_idle(
    pet_window: QQuickWindow,
    pet_body: QQuickItem,
    compact_window: QQuickItem,
) -> dict[str, object]:
    """Move the synthetic pointer away and require the real idle contract.

    QTest does not have a window-system cursor shared by sibling offscreen
    windows.  After clicking Lilith, opening the world and pressing its exit
    button, her HoverHandler can therefore retain the last pet-window hover
    forever.  A physical pointer necessarily left the pet to reach that exit
    button.  Send the equivalent window Leave event and render bounded
    frames; never write ``lowPower`` or relax its production conditions.
    """

    started_at = time.monotonic()
    deadline = started_at + 1.5
    attempts = 0
    # QTest's offscreen mouseMove does not synthesize the platform leave that
    # a real cursor transition to the world/chat sibling window produces.
    # Deliver that public Qt window event rather than mutating the QML hover or
    # low-power properties.  An in-window QTest.mouseMove is deliberately not
    # used here: the offscreen QPA can treat it as a fresh enter even when its
    # coordinate is outside the containment mask.
    QApplication.sendEvent(pet_window, QEvent(QEvent.Type.Leave))
    while (
        not bool(pet_body.property("lowPower"))
        and time.monotonic() < deadline
    ):
        attempts += 1
        _pump_quick_frame(pet_window, 20)
    return {
        "satisfied": bool(pet_body.property("lowPower")),
        "attempts": attempts,
        "elapsedMs": round((time.monotonic() - started_at) * 1000, 1),
        "highMotion": bool(compact_window.property("highMotion")),
        "poseTransitionRunning": bool(
            pet_body.property("poseTransitionRunning")
        ),
        "characterHovered": bool(pet_body.property("characterHovered")),
        "targetFps": int(pet_body.property("targetFps") or 0),
    }


def _settle_radial_collapsed(
    pet_window: QQuickWindow,
    compact_window: QQuickItem,
) -> dict[str, object]:
    """Require the previous real action's closing orbit before reopening."""

    started_at = time.monotonic()
    deadline = started_at + 1.5
    attempts = 0
    while (
        (
            bool(compact_window.property("expanded"))
            or float(compact_window.property("orbitProgress") or 0.0) > 0.01
        )
        and time.monotonic() < deadline
    ):
        attempts += 1
        _pump_quick_frame(pet_window, 20)
    expanded = bool(compact_window.property("expanded"))
    orbit_progress = float(compact_window.property("orbitProgress") or 0.0)
    return {
        "satisfied": not expanded and orbit_progress <= 0.01,
        "attempts": attempts,
        "elapsedMs": round((time.monotonic() - started_at) * 1000, 1),
        "expanded": expanded,
        "orbitProgress": round(orbit_progress, 4),
    }


def _click_character_menu(
    pet_window: QQuickWindow,
    compact_window: QQuickItem,
    hit_test: CompactHitTestFilter,
) -> dict[str, object]:
    point = _character_point(pet_window)
    pet_body = pet_window.findChild(QQuickItem, "compactLilith")
    result: dict[str, object] = {
        "characterPoint": [point.x(), point.y()],
        "characterFilterHit": bool(hit_test.accepts_point(point.x(), point.y())),
        "transparentCornerPassesThrough": not bool(hit_test.accepts_point(1, 1)),
        "lowPowerBeforeClick": bool(
            pet_body.property("lowPower")
        ),
        "motionBeforeClick": {
            "highMotion": bool(compact_window.property("highMotion")),
            "poseTransitionRunning": bool(
                pet_body.property("poseTransitionRunning")
            ),
            "characterHovered": bool(pet_body.property("characterHovered")),
            "characterPressed": bool(pet_body.property("characterPressed")),
            "targetFps": int(pet_body.property("targetFps") or 0),
        },
    }
    QTest.mouseClick(pet_window, Qt.MouseButton.LeftButton, pos=point)
    wait_started_at = time.monotonic()
    wait_deadline = wait_started_at + 1.5
    wait_attempts = 0
    visible_transition_samples = 0
    transition_mismatches: list[dict[str, object]] = []
    while (
        (
            not bool(compact_window.property("expanded"))
            or not bool(compact_window.property("actionsInteractive"))
            or float(compact_window.property("orbitProgress") or 0.0) < 0.999
        )
        and time.monotonic() < wait_deadline
    ):
        wait_attempts += 1
        _pump_quick_frame(pet_window, 20)
        actions_interactive = bool(
            compact_window.property("actionsInteractive")
        )
        for item in CompactHitTestFilter._visual_descendants(
            pet_window.contentItem()
        ):
            object_name = item.objectName()
            if not object_name.startswith("desktopPetAction_") or not item.isVisible():
                continue
            action_id = object_name.removeprefix("desktopPetAction_")
            action_hit = _named_item(
                pet_window, f"desktopPetActionHit_{action_id}"
            )
            action_point = _item_center(item)
            qml_enabled = bool(action_hit and action_hit.isEnabled())
            native_hit = bool(
                hit_test.accepts_point(action_point.x(), action_point.y())
            )
            visible_transition_samples += 1
            if not (actions_interactive and qml_enabled and native_hit):
                transition_mismatches.append(
                    {
                        "action": action_id,
                        "orbitProgress": round(
                            float(
                                compact_window.property("orbitProgress") or 0.0
                            ),
                            4,
                        ),
                        "actionsInteractive": actions_interactive,
                        "qmlEnabled": qml_enabled,
                        "nativeHit": native_hit,
                    }
                )
    result.update(
        menuExpanded=bool(compact_window.property("expanded")),
        actionsInteractive=bool(compact_window.property("actionsInteractive")),
        menuWait={
            "satisfied": bool(compact_window.property("expanded"))
            and bool(compact_window.property("actionsInteractive")),
            "settled": float(
                compact_window.property("orbitProgress") or 0.0
            ) >= 0.999,
            "attempts": wait_attempts,
            "elapsedMs": round(
                (time.monotonic() - wait_started_at) * 1000, 1
            ),
            "orbitProgress": round(
                float(compact_window.property("orbitProgress") or 0.0), 4
            ),
        },
        transitionHitContract={
            "visibleSamples": visible_transition_samples,
            "mismatches": transition_mismatches,
            "satisfied": visible_transition_samples > 0
            and not transition_mismatches,
        },
    )
    return result


def _click_action(
    pet_window: QQuickWindow,
    action_id: str,
    hit_test: CompactHitTestFilter,
) -> dict[str, object]:
    item = _action(pet_window, action_id)
    lookup_started_at = time.monotonic()
    lookup_deadline = lookup_started_at + 1.5
    lookup_attempts = 0
    while item is None and time.monotonic() < lookup_deadline:
        lookup_attempts += 1
        _pump_quick_frame(pet_window, 20)
        item = _action(pet_window, action_id)
    if item is None:
        return {
            "actionFound": False,
            "actionVisible": False,
            "actionEnabled": False,
            "filterHit": False,
            "offscreenRouteUnobstructed": False,
            "clicked": False,
            "lookupAttempts": lookup_attempts,
            "lookupElapsedMs": round(
                (time.monotonic() - lookup_started_at) * 1000, 1
            ),
        }
    point = _item_center(item)
    global_point = pet_window.mapToGlobal(point)
    covering_window = QGuiApplication.topLevelAt(global_point)
    result: dict[str, object] = {
        "actionFound": True,
        "actionVisible": bool(item.isVisible()),
        "actionEnabled": bool(item.isEnabled()),
        "filterHit": bool(hit_test.accepts_point(point.x(), point.y())),
        "localPoint": [point.x(), point.y()],
        "globalPoint": [global_point.x(), global_point.y()],
        "topLevelAtPoint": _window_name(covering_window),
        # Offscreen has no window-system z-order and may report None.  This
        # check only rules out another Qt top-level in this synthetic scene;
        # the packaged hidden-qwindows gate separately dispatches the real
        # WM_NCHITTEST and requires HTCLIENT for this radial action.
        "offscreenRouteUnobstructed": covering_window in {None, pet_window},
        "lookupAttempts": lookup_attempts,
        "lookupElapsedMs": round(
            (time.monotonic() - lookup_started_at) * 1000, 1
        ),
    }
    QTest.mouseClick(pet_window, Qt.MouseButton.LeftButton, pos=point)
    QTest.qWait(280)
    result["clicked"] = True
    return result


def _click_named_item(window: QQuickWindow, object_name: str) -> bool:
    item = _named_item(window, object_name)
    if item is None or not item.isVisible() or not item.isEnabled():
        return False
    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        pos=_item_center(item),
    )
    QTest.qWait(160)
    return True


def _replace_text(
    window: QQuickWindow, object_name: str, text: str
) -> bool:
    item = _named_item(window, object_name)
    if item is None or not item.isVisible() or not item.isEnabled():
        return False
    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        pos=_item_center(item),
    )
    # QTest exposes text entry only for QWidget, while this surface is a
    # QQuickWindow.  The focus click above is still real; seed the deterministic
    # test string through the public TextField property, then exercise every
    # state-changing button through QTest below.
    item.setProperty("text", text)
    QTest.qWait(80)
    return str(item.property("text") or "") == text


def _exercise_daily_loop(
    backend: Backend, work_panel: QQuickWindow
) -> dict[str, object]:
    """Exercise real QML controls from task creation through deterministic unlock."""

    category = _named_item(work_panel, "workPanelTaskCategoryInput")
    result: dict[str, object] = {
        "categoryFound": category is not None,
        "categoryDefault": str(category.property("currentText") or "")
        if category is not None
        else "",
        "createdByClick": 0,
        "completedByClick": 0,
    }
    for index in range(3):
        title = f"Daily loop {index + 1}"
        if not _replace_text(work_panel, "workPanelTaskTitleInput", title):
            continue
        if not _click_named_item(work_panel, "workPanelTaskCreateButton"):
            continue
        task = next(
            (
                value
                for value in backend.taskItems
                if str(value.get("title", "")) == title
            ),
            None,
        )
        if task is None:
            continue
        result["createdByClick"] = int(result["createdByClick"]) + 1
        if str(task.get("category", "")) != "daily":
            continue
        if _click_named_item(
            work_panel, f"workPanelTaskComplete_{task.get('id', '')}"
        ):
            result["completedByClick"] = int(result["completedByClick"]) + 1

    growth = backend.growthStatus
    unlock_keys = {
        str(value.get("item_key", "")) for value in growth.get("unlocks", [])
    }
    result.update(
        points=int(growth.get("points", 0)),
        homeCardiganUnlocked="outfit:home-cardigan" in unlock_keys,
        livingCornerUnlocked="world:living-corner" in unlock_keys,
    )

    # A reached stage never regresses when a task is reopened and its points
    # are compensated.  Recreate that authoritative projection directly in
    # this isolated database: the QML must consume growthStatus.stage instead
    # of deriving a lower stage from the score again.
    with backend.database.connect() as connection:
        connection.execute(
            "UPDATE growth_state SET stage='熟悉' WHERE state_id='default'"
        )
    backend.productivityChanged.emit()
    QTest.qWait(120)
    authoritative = backend.growthStatus
    result["authoritativeProjection"] = {
        "backendStage": str(authoritative.get("stage", "")),
        "qmlStage": str(work_panel.property("resonanceStage") or ""),
        "backendNextStage": str(authoritative.get("nextStage", "")),
        "qmlNextStage": str(work_panel.property("resonanceNextStage") or ""),
        "backendNextAt": authoritative.get("nextAt"),
        "qmlNextAt": work_panel.property("resonanceNextAt"),
        "backendProgress": float(authoritative.get("progress", 0.0)),
        "qmlProgress": float(work_panel.property("resonanceProgress") or 0.0),
    }

    reminder_title = "Daily loop reminder"
    reminder_typed = _replace_text(
        work_panel, "workPanelReminderTitleInput", reminder_title
    )
    reminder_created = reminder_typed and _click_named_item(
        work_panel, "workPanelReminderCreateButton"
    )
    pending = [
        value
        for value in backend.reminderItems
        if str(value.get("title", "")) == reminder_title
    ]
    reminder_id = str(pending[0].get("id", "")) if pending else ""
    reminder_dismissed = bool(reminder_id) and _click_named_item(
        work_panel, f"workPanelReminderDismiss_{reminder_id}"
    )
    snooze = (
        _named_item(work_panel, f"workPanelReminderSnooze_{reminder_id}")
        if reminder_id
        else None
    )
    result["reminder"] = {
        "createdByClick": bool(reminder_created and reminder_id),
        "dismissedByClick": reminder_dismissed,
        "pendingProjectionEmpty": not any(
            str(value.get("id", "")) == reminder_id
            for value in backend.reminderItems
        ),
        "endedSnoozeUnavailable": snooze is None
        or not snooze.isVisible()
        or not snooze.isEnabled(),
    }
    return result


def _seed_companion_bubble(backend: Backend) -> None:
    backend.companion._bubble = {
        "id": "box-world-click-path-bubble",
        "category": "science",
        "summary": "Offscreen companion bubble used to verify click routing.",
        "detail": "No model, screenshot, network, or foreground application is used.",
        "source": {},
        "actions": [],
        "sceneLabel": "offscreen",
        "createdAt": "2026-08-31T00:00:00Z",
        "expiresAt": "",
        "visible": True,
        "busy": False,
    }
    backend.companion.bubbleChanged.emit()
    QTest.qWait(160)


def _close_world_from_scene(
    backend: Backend,
    world_scene: QQuickWindow,
    pet_window: QQuickWindow,
) -> dict[str, object]:
    exit_action = _named_item(world_scene, "boxWorldSceneExitButton")
    clicked = False
    if exit_action is not None and world_scene.isVisible():
        QTest.mouseClick(
            world_scene,
            Qt.MouseButton.LeftButton,
            pos=_item_center(exit_action),
        )
        clicked = True
        QTest.qWait(180)
    return {
        "exitFound": exit_action is not None,
        "exitClicked": clicked,
        "backendClosed": not bool(backend.boxWorldSceneOpen),
        "sceneHidden": not bool(world_scene.isVisible()),
        "petReturned": bool(pet_window.isVisible()),
        "petRejectsFocus": bool(
            pet_window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
        "petDidNotTakeFocus": QGuiApplication.focusWindow() is not pet_window,
    }


def _world_round(
    *,
    mode: str,
    backend: Backend,
    pet_window: QQuickWindow,
    pet_body: QQuickItem,
    compact_window: QQuickItem,
    world_scene: QQuickWindow,
    companion_bubble: QQuickWindow,
    hit_test: CompactHitTestFilter,
) -> dict[str, object]:
    backend.setPetFloatMode(mode)
    backend.setChatOpen(False)
    backend.setWorkPanelOpen(False)
    backend.setBoxWorldSceneOpen(False)
    compact_window.setProperty("expanded", False)
    radial_settle = _settle_radial_collapsed(pet_window, compact_window)
    _seed_companion_bubble(backend)
    QTest.qWait(760)
    idle_settle = _settle_pet_idle(pet_window, pet_body, compact_window)

    flags = pet_window.flags()
    result: dict[str, object] = {
        "mode": mode,
        "petVisible": bool(pet_window.isVisible()),
        "bubbleVisibleBeforeMenu": bool(companion_bubble.isVisible()),
        "petRejectsFocus": bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus),
        "topmostMatchesMode": bool(flags & Qt.WindowType.WindowStaysOnTopHint)
        == (mode == "always"),
        "petLowPowerBeforeMenu": bool(pet_body.property("lowPower")),
        "radialSettle": radial_settle,
        "idleSettle": idle_settle,
    }
    result["menu"] = _click_character_menu(
        pet_window, compact_window, hit_test
    )
    result["bubbleVisibleDuringMenu"] = bool(companion_bubble.isVisible())
    result["worldClick"] = _click_action(pet_window, "world", hit_test)
    result["presentation"] = {
        "entered": bool(backend.boxWorldStatus.get("entered")),
        "backendOpen": bool(backend.boxWorldSceneOpen),
        "sceneVisible": bool(world_scene.isVisible()),
        "sceneExposed": bool(world_scene.isExposed()),
        "sceneNotMinimized": world_scene.visibility()
        != QWindow.Visibility.Minimized,
        "presentationCount": int(world_scene.property("presentationCount") or 0),
        "menuClosed": not bool(compact_window.property("expanded")),
        "bubbleStillAvailable": bool(companion_bubble.isVisible()),
    }
    result["close"] = _close_world_from_scene(backend, world_scene, pet_window)
    backend.companion.dismissExplicit()
    QTest.qWait(100)
    return result


def _open_action(
    action_id: str,
    *,
    pet_window: QQuickWindow,
    compact_window: QQuickItem,
    hit_test: CompactHitTestFilter,
) -> dict[str, object]:
    radial_settle = _settle_radial_collapsed(pet_window, compact_window)
    pet_body = pet_window.findChild(QQuickItem, "compactLilith")
    idle_settle = _settle_pet_idle(
        pet_window, pet_body, compact_window
    )
    menu = _click_character_menu(pet_window, compact_window, hit_test)
    action = _click_action(pet_window, action_id, hit_test)
    return {
        "radialSettle": radial_settle,
        "idleSettle": idle_settle,
        "menu": menu,
        "action": action,
    }


def _run_verification(app: QApplication, backend: Backend) -> tuple[bool, dict[str, object]]:
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(
        lambda values: warnings.extend(str(value.toString()) for value in values)
    )
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml failed to load: " + " | ".join(warnings[-8:]))

    root = engine.rootObjects()[0]
    pet_window = root.findChild(QQuickWindow, "petWindow")
    pet_body = root.findChild(QQuickItem, "compactLilith")
    compact_window = root.findChild(QQuickItem, "desktopPet")
    world_scene = root.findChild(QQuickWindow, "boxWorldSceneWindow")
    companion_bubble = root.findChild(QQuickWindow, "companionBubbleWindow")
    chat_window = root.findChild(QQuickWindow, "chatWindow")
    work_panel = root.findChild(QQuickWindow, "v03WorkPanel")
    if any(
        value is None
        for value in (
            pet_window,
            pet_body,
            compact_window,
            world_scene,
            companion_bubble,
            chat_window,
            work_panel,
        )
    ):
        raise RuntimeError("full compact click-path surface failed to load")

    hit_test = CompactHitTestFilter(
        pet_window,
        backend,
        native_move_controller=(
            pointer_event_filter := CompactPointerEventFilter(pet_window)
        ),
        native_window_id=int(pet_window.winId()),
    )
    app.installNativeEventFilter(hit_test)
    pet_window.setProperty("nativeMoveController", pointer_event_filter)
    pet_window.installEventFilter(pointer_event_filter)
    # Keep the bridge alive for the whole QML verification.  A production
    # launch stores the same object on QApplication for exactly this reason.
    app._lilies_click_path_pointer_filter = pointer_event_filter
    app._lilies_click_path_hit_test_filter = hit_test
    QTest.qWait(900)

    status_toast = _named_item(pet_window, "backendStatusToast")
    status_text = _named_item(pet_window, "backendStatusToastText")
    focus_before_status = QGuiApplication.focusWindow()
    backend._set_status("Offscreen status feedback")
    QTest.qWait(120)
    status_point = _item_center(status_toast) if status_toast is not None else QPoint()
    status_feedback = {
        "found": status_toast is not None and status_text is not None,
        "visible": bool(status_toast and status_toast.isVisible()),
        "text": str(status_text.property("text") or "") if status_text else "",
        "doesNotTakeFocus": QGuiApplication.focusWindow() is focus_before_status,
        "passesThrough": not bool(
            status_toast
            and hit_test.accepts_point(status_point.x(), status_point.y())
        ),
    }

    report: dict[str, object] = {
        "statusFeedback": status_feedback,
        "always": _world_round(
            mode="always",
            backend=backend,
            pet_window=pet_window,
            pet_body=pet_body,
            compact_window=compact_window,
            world_scene=world_scene,
            companion_bubble=companion_bubble,
            hit_test=hit_test,
        ),
        "normal": _world_round(
            mode="normal",
            backend=backend,
            pet_window=pet_window,
            pet_body=pet_body,
            compact_window=compact_window,
            world_scene=world_scene,
            companion_bubble=companion_bubble,
            hit_test=hit_test,
        ),
    }

    # The user's persisted layout can legitimately sit at the 110 DIP floor.
    # Re-run the complete world entry/exit route there: a button that works
    # only at the roomy default pet size is still a dead feature in practice.
    root.setProperty("preferredCompactBoxSize", 110.0)
    root.setProperty("compactBoxSize", 110.0)
    backend.setCompactPetEffectiveSize(110.0)
    QTest.qWait(260)
    report["minimumSize"] = _world_round(
        mode="always",
        backend=backend,
        pet_window=pet_window,
        pet_body=pet_body,
        compact_window=compact_window,
        world_scene=world_scene,
        companion_bubble=companion_bubble,
        hit_test=hit_test,
    )

    # A bubble that expired unseen must leave a small, persistent click
    # target on Lilith.  Exercise the real native hit-mask path at the same
    # minimum size used above; a missing historical row deliberately routes
    # to the visible companion status page instead of looking like a dead dot.
    saved_delivery = dict(backend.companion._delivery_record)
    backend.companion._delivery_record = {
        "schemaVersion": 1,
        "sessionId": "missing-offscreen-session",
        "bubbleId": "missing-offscreen-bubble",
        "state": "unread",
        "reason": "offscreen-click-route",
        "generatedAt": "2026-08-31T00:00:00Z",
        "presentedAt": "",
        "expiresAt": "",
        "unread": True,
    }
    backend.companion.changed.emit()
    QTest.qWait(140)
    unread_cue = _named_item(pet_window, "desktopPetCompanionUnreadCue")
    unread_result: dict[str, object] = {
        "found": unread_cue is not None,
        "visible": bool(unread_cue and unread_cue.isVisible()),
        "filterHit": False,
        "clicked": False,
        "openedStatus": False,
        "page": -1,
    }
    if unread_cue is not None and unread_cue.isVisible():
        unread_point = _item_center(unread_cue)
        unread_result["filterHit"] = bool(
            hit_test.accepts_point(unread_point.x(), unread_point.y())
        )
        QTest.mouseClick(
            pet_window, Qt.MouseButton.LeftButton, pos=unread_point
        )
        unread_result["clicked"] = True
        QTest.qWait(420)
        unread_result["openedStatus"] = bool(
            backend.chatOpen and chat_window.isVisible()
        )
        unread_result["page"] = int(chat_window.property("page") or 0)
    report["unreadCue"] = unread_result
    backend.setChatOpen(False)
    backend.companion._delivery_record = saved_delivery
    backend.companion.changed.emit()
    QTest.qWait(120)

    backend.setPetFloatMode("always")
    chat_route = _open_action(
        "chat",
        pet_window=pet_window,
        compact_window=compact_window,
        hit_test=hit_test,
    )
    chat_route["opened"] = bool(backend.chatOpen and chat_window.isVisible())
    chat_route["page"] = int(chat_window.property("page") or 0)
    chat_route["exposed"] = bool(chat_window.isExposed())
    backend.setChatOpen(False)
    QTest.qWait(160)
    report["chat"] = chat_route

    settings_route = _open_action(
        "settings",
        pet_window=pet_window,
        compact_window=compact_window,
        hit_test=hit_test,
    )
    settings_route["opened"] = bool(backend.chatOpen and chat_window.isVisible())
    settings_route["page"] = int(chat_window.property("page") or 0)
    QTest.qWait(260)
    function_pin = _named_item(chat_window, "functionLibraryPin_work")
    pin_visible = bool(function_pin and function_pin.isVisible())
    pin_enabled = bool(function_pin and function_pin.isEnabled())
    pin_clicked = False
    if function_pin is not None and pin_visible and pin_enabled:
        QTest.mouseClick(
            chat_window,
            Qt.MouseButton.LeftButton,
            pos=_item_center(function_pin),
        )
        pin_clicked = True
        QTest.qWait(220)
    companion_pin = _named_item(chat_window, "functionLibraryPin_companion")
    companion_pin_visible = bool(companion_pin and companion_pin.isVisible())
    companion_pin_enabled = bool(companion_pin and companion_pin.isEnabled())
    companion_pin_clicked = False
    if companion_pin is not None and companion_pin_visible and companion_pin_enabled:
        QTest.mouseClick(
            chat_window,
            Qt.MouseButton.LeftButton,
            pos=_item_center(companion_pin),
        )
        companion_pin_clicked = True
        QTest.qWait(220)
    work_up = _named_item(chat_window, "functionLibraryMoveUp_work")
    work_down = _named_item(chat_window, "functionLibraryMoveDown_work")
    companion_up = _named_item(chat_window, "functionLibraryMoveUp_companion")
    companion_down = _named_item(
        chat_window, "functionLibraryMoveDown_companion"
    )
    settings_route["functionLibrary"] = {
        "pinFound": function_pin is not None,
        "pinVisible": pin_visible,
        "pinEnabled": pin_enabled,
        "pinClicked": pin_clicked,
        "workSelected": any(
            str(value.get("action", "")) == "work"
            for value in backend.quickActions
        ),
        "companionPinFound": companion_pin is not None,
        "companionPinVisible": companion_pin_visible,
        "companionPinEnabled": companion_pin_enabled,
        "companionPinClicked": companion_pin_clicked,
        "companionSelected": any(
            str(value.get("action", "")) == "companion"
            for value in backend.quickActions
        ),
        "firstOptionalCannotMoveUp": bool(
            work_up is not None and not work_up.isEnabled()
        ),
        "firstOptionalCanMoveDown": bool(
            work_down is not None and work_down.isEnabled()
        ),
        "lastOptionalCanMoveUp": bool(
            companion_up is not None and companion_up.isEnabled()
        ),
        "lastOptionalCannotMoveDown": bool(
            companion_down is not None and not companion_down.isEnabled()
        ),
    }
    backend.setChatOpen(False)
    QTest.qWait(180)
    report["settings"] = settings_route

    work_route = _open_action(
        "work",
        pet_window=pet_window,
        compact_window=compact_window,
        hit_test=hit_test,
    )
    work_route["opened"] = bool(backend.workPanelOpen and work_panel.isVisible())
    work_route["section"] = str(backend.workPanelSection)
    work_route["exposed"] = bool(work_panel.isExposed())
    work_route["dailyLoop"] = _exercise_daily_loop(backend, work_panel)
    backend.setWorkPanelOpen(False)
    QTest.qWait(100)
    report["functionLibraryAction"] = work_route

    # A rejected manual companion request must route to its visible status
    # page instead of looking like a dead radial action.  Pausing avoids any
    # model/network work while still exercising the real QML click chain.
    backend.companion.setPaused(True)
    companion_route = _open_action(
        "companion",
        pet_window=pet_window,
        compact_window=compact_window,
        hit_test=hit_test,
    )
    companion_route["openedStatus"] = bool(
        backend.chatOpen and chat_window.isVisible()
    )
    companion_route["page"] = int(chat_window.property("page") or 0)
    companion_route["feedback"] = str(
        backend.companion.activityStatus.get("requestFeedback", "")
    )
    backend.setChatOpen(False)
    backend.companion.setPaused(False)
    QTest.qWait(100)
    report["companionFunctionAction"] = companion_route

    def menu_ok(value: dict[str, object]) -> bool:
        menu = value["menu"]
        return all(
            (
                menu["characterFilterHit"],
                menu["transparentCornerPassesThrough"],
                menu["lowPowerBeforeClick"],
                menu["menuExpanded"],
                menu["actionsInteractive"],
                menu["menuWait"]["satisfied"],
                menu["menuWait"]["settled"],
                menu["transitionHitContract"]["satisfied"],
            )
        )

    def action_ok(value: dict[str, object]) -> bool:
        action = value["action"]
        return all(
            (
                action["actionFound"],
                action["actionVisible"],
                action["actionEnabled"],
                action["filterHit"],
                action["offscreenRouteUnobstructed"],
                action["clicked"],
            )
        )

    world_rounds_ok = True
    for mode in ("always", "normal", "minimumSize"):
        value = report[mode]
        click = value["worldClick"]
        presentation = value["presentation"]
        close = value["close"]
        world_rounds_ok = world_rounds_ok and all(
            (
                value["petVisible"],
                value["bubbleVisibleBeforeMenu"],
                value["petRejectsFocus"],
                value["topmostMatchesMode"],
                value["petLowPowerBeforeMenu"],
                value["radialSettle"]["satisfied"],
                value["idleSettle"]["satisfied"],
                menu_ok(value),
                click["actionFound"],
                click["actionVisible"],
                click["actionEnabled"],
                click["filterHit"],
                click["offscreenRouteUnobstructed"],
                click["clicked"],
                presentation["entered"],
                presentation["backendOpen"],
                presentation["sceneVisible"],
                presentation["sceneExposed"],
                presentation["sceneNotMinimized"],
                presentation["presentationCount"] >= 1,
                presentation["menuClosed"],
                presentation["bubbleStillAvailable"],
                *close.values(),
            )
        )

    chat_ok = menu_ok(chat_route) and action_ok(chat_route) and all(
        (
            chat_route["radialSettle"]["satisfied"],
            chat_route["idleSettle"]["satisfied"],
            chat_route["opened"],
            chat_route["page"] == 0,
            chat_route["exposed"],
        )
    )
    library = settings_route["functionLibrary"]
    settings_ok = menu_ok(settings_route) and action_ok(settings_route) and all(
        (
            settings_route["opened"],
            settings_route["page"] == 4,
            settings_route["radialSettle"]["satisfied"],
            settings_route["idleSettle"]["satisfied"],
            *library.values(),
        )
    )
    work_ok = menu_ok(work_route) and action_ok(work_route) and all(
        (
            work_route["radialSettle"]["satisfied"],
            work_route["idleSettle"]["satisfied"],
            work_route["opened"],
            work_route["section"] == "work",
            work_route["exposed"],
            work_route["dailyLoop"]["categoryFound"],
            work_route["dailyLoop"]["categoryDefault"] == "日常",
            work_route["dailyLoop"]["createdByClick"] == 3,
            work_route["dailyLoop"]["completedByClick"] == 3,
            work_route["dailyLoop"]["points"] == 30,
            work_route["dailyLoop"]["homeCardiganUnlocked"],
            work_route["dailyLoop"]["livingCornerUnlocked"],
            work_route["dailyLoop"]["authoritativeProjection"]["backendStage"]
            == work_route["dailyLoop"]["authoritativeProjection"]["qmlStage"],
            work_route["dailyLoop"]["authoritativeProjection"]["backendNextStage"]
            == work_route["dailyLoop"]["authoritativeProjection"]["qmlNextStage"],
            work_route["dailyLoop"]["authoritativeProjection"]["backendNextAt"]
            == work_route["dailyLoop"]["authoritativeProjection"]["qmlNextAt"],
            abs(
                work_route["dailyLoop"]["authoritativeProjection"]["backendProgress"]
                - work_route["dailyLoop"]["authoritativeProjection"]["qmlProgress"]
            )
            < 0.0001,
            *work_route["dailyLoop"]["reminder"].values(),
        )
    )
    companion_ok = menu_ok(companion_route) and action_ok(companion_route) and all(
        (
            companion_route["radialSettle"]["satisfied"],
            companion_route["idleSettle"]["satisfied"],
            companion_route["openedStatus"],
            companion_route["page"] == 3,
            bool(companion_route["feedback"]),
        )
    )
    unread_ok = all(
        (
            report["unreadCue"]["found"],
            report["unreadCue"]["visible"],
            report["unreadCue"]["filterHit"],
            report["unreadCue"]["clicked"],
            report["unreadCue"]["openedStatus"],
            report["unreadCue"]["page"] == 3,
        )
    )
    report["passed"] = bool(
        all(report["statusFeedback"].values())
        and world_rounds_ok
        and chat_ok
        and settings_ok
        and work_ok
        and companion_ok
        and unread_ok
    )

    backend.setBoxWorldSceneOpen(False)
    backend.setChatOpen(False)
    backend.setWorkPanelOpen(False)
    backend.companion.dismissExplicit()
    QTest.qWait(40)
    return bool(report["passed"]), report


def main() -> int:
    previous_data_dir = os.environ.get("LILIES_DATA_DIR")
    backend: Backend | None = None
    app: QApplication | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="lilies-click-path-") as data_dir:
            os.environ["LILIES_DATA_DIR"] = data_dir
            try:
                QQuickWindow.setDefaultAlphaBuffer(True)
                app = QApplication([])
                backend = Backend(smoke=True, force_compact=True)
                backend._v03_timer.stop()
                passed, report = _run_verification(app, backend)
                print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                try:
                    if backend is not None:
                        backend.shutdown()
                        backend = None
                finally:
                    if app is not None:
                        native_filter = getattr(
                            app, "_lilies_click_path_hit_test_filter", None
                        )
                        if native_filter is not None:
                            app.removeNativeEventFilter(native_filter)
                            app._lilies_click_path_hit_test_filter = None
                        app.processEvents()
            return 0 if passed else 1
    finally:
        if backend is not None:
            backend.shutdown()
        if app is not None:
            app.quit()
        if previous_data_dir is None:
            os.environ.pop("LILIES_DATA_DIR", None)
        else:
            os.environ["LILIES_DATA_DIR"] = previous_data_dir


if __name__ == "__main__":
    raise SystemExit(main())
