from __future__ import annotations

import json
import itertools
import math
import os
import sys
import tempfile
import time
from pathlib import Path

# This verifier constructs native QQuickWindow objects, so force Qt's headless
# platform before importing PySide.  It never enumerates or captures the user's
# real windows.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QMouseEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.app import CompactHitTestFilter, CompactPointerEventFilter
from lilies.core.pet_habitat import choose_habitat_candidate
from lilies.core.window_catalog import WindowRect
from lilies.paths import qml_path
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts


def _area(left: int, top: int, width: int, height: int, name: str) -> dict[str, object]:
    return {
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
        "width": width,
        "height": height,
        "name": name,
        "devicePixelRatio": 1.0,
    }


def _inside(value: float, low: float, high: float, tolerance: float = 1.1) -> bool:
    return low - tolerance <= value <= high + tolerance


def _overlap_area(first: list[float], second: list[float]) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    return width * height


def _item_center_in_window(
    item: QQuickItem, pet_window: QQuickWindow
) -> QPointF:
    return item.mapToItem(
        pet_window.contentItem(),
        QPointF(float(item.width()) / 2.0, float(item.height()) / 2.0),
    )


def _item_global_center(
    item: QQuickItem, pet_window: QQuickWindow
) -> tuple[float, float]:
    center = _item_center_in_window(item, pet_window)
    return (
        float(pet_window.x()) + float(center.x()),
        float(pet_window.y()) + float(center.y()),
    )


def _wait_for_rendered_item_center(
    item: QQuickItem,
    pet_window: QQuickWindow,
    app: QApplication,
    expected_x: float,
    expected_y: float,
    *,
    timeout_ms: int = 180,
) -> tuple[float, float]:
    """Wait for the offscreen animation driver to render a staged drag.

    FrameAnimation is coupled to the real display clock in production.  Qt's
    offscreen plugin can legally skip a requested tick, even when qWait() has
    elapsed, so a fixed sleep occasionally observes the preceding rendered
    sample.  Keep requesting actual Quick frames until the strict geometric
    target is visible or the short deadline expires; a clamped or incorrect
    target still returns with its full error and fails the caller's assertion.
    """

    deadline = time.monotonic() + timeout_ms / 1000.0
    center = _item_global_center(item, pet_window)
    while (
        abs(center[0] - expected_x) > 1.1
        or abs(center[1] - expected_y) > 1.1
    ) and time.monotonic() < deadline:
        pet_window.requestUpdate()
        QTest.qWait(8)
        app.processEvents()
        pet_window.grabWindow()
        app.processEvents()
        center = _item_global_center(item, pet_window)
    return center


def _reveal_resize_handle_from_character_hover(
    pet_window: QQuickWindow, resize_handle: QQuickItem, app: QApplication
) -> bool:
    """Arm the production hover affordance before exercising its DragHandler.

    The resize handle is intentionally hidden until the pointer first reaches
    Lilith.  Sending a synthetic move straight to an invisible handle relies
    on stale hover state and stopped working once the character mask became
    more precise.  Probe real accepted silhouette points, then move onto the
    now-visible handle exactly as a user does.
    """

    left = float(pet_window.property("compactCharacterLeft") or 0.0)
    top = float(pet_window.property("compactCharacterTop") or 0.0)
    width = float(pet_window.property("compactCharacterWidth") or 0.0)
    height = float(pet_window.property("compactCharacterHeight") or 0.0)
    contains = getattr(pet_window, "characterContains", None)
    if width <= 0 or height <= 0 or not callable(contains):
        return False

    # Prefer the head and centreline, then cover the full declared figure.
    candidates = (
        (0.50, 0.20),
        (0.50, 0.34),
        (0.50, 0.50),
        (0.38, 0.28),
        (0.62, 0.28),
        (0.38, 0.52),
        (0.62, 0.52),
        (0.50, 0.72),
    )
    for nx, ny in candidates:
        x = left + width * nx
        y = top + height * ny
        try:
            accepted = bool(contains(x, y))
        except (RuntimeError, TypeError, ValueError):
            accepted = False
        if not accepted:
            continue
        QTest.mouseMove(pet_window, QPoint(round(x), round(y)), 2)
        QTest.qWait(24)
        app.processEvents()
        if resize_handle.isVisible():
            return True
    return False


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="lilies-cross-dpi-")
    os.environ["LILIES_DATA_DIR"] = temporary.name
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([])
    load_windows_ui_fonts()
    backend = OffscreenBackend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    # Use production gesture decisions while the QPA remains forcibly
    # offscreen; previewMode would deliberately skip all native requests.
    backend._preview_mode = False
    backend.database.set_setting(
        "desktop_pet_quick_actions_v1", ["focus", "peek", "reading"]
    )
    # Exercise both old persisted extrema and each cardinal direction.  These
    # values are legal according to Backend.saveComponentLayout.
    backend.database.set_setting(
        "desktop_pet_component_layout_v3",
        {
            "chat": {"dx": 1.48, "dy": -1.42, "scale": 1.55},
            "world": {"dx": -1.48, "dy": 1.42, "scale": 1.55},
            "settings": {"dx": 0.0, "dy": -1.42, "scale": 1.55},
            "focus": {"dx": 1.48, "dy": 1.42, "scale": 1.55},
            "peek": {"dx": -1.48, "dy": -1.42, "scale": 1.55},
            "reading": {"dx": 0.0, "dy": 1.42, "scale": 1.55},
        },
    )

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml failed to load in offscreen verifier")
    root = engine.rootObjects()[0]
    pet_window = root.findChild(QQuickWindow, "petWindow")
    pet_body = root.findChild(QQuickItem, "compactLilith")
    compact_window = root.findChild(QQuickItem, "desktopPet")
    focus_aura = root.findChild(QQuickWindow, "v03FocusTimerAura")
    if (
        pet_window is None
        or pet_body is None
        or compact_window is None
        or focus_aura is None
    ):
        raise RuntimeError("cross-DPI layout targets did not load")
    pointer_event_filter = CompactPointerEventFilter(pet_window)
    if not pet_window.setProperty("nativeMoveController", pointer_event_filter):
        raise RuntimeError("drag position bridge was not installed")
    hit_test_filter = CompactHitTestFilter(
        pet_window,
        backend,
        pointer_event_filter,
        native_window_id=int(pet_window.winId()),
    )

    descendants = tuple(
        CompactHitTestFilter._visual_descendants(pet_window.contentItem())
    )
    action_items = sorted(
        (
            item
            for item in descendants
            if str(item.objectName()).startswith("desktopPetAction_")
        ),
        key=lambda item: str(item.objectName()),
    )
    if len(action_items) != 6:
        raise RuntimeError(f"expected six radial actions, got {len(action_items)}")
    accessory_box = next(
        (
            item
            for item in descendants
            if item.objectName() == "compactAccessoryBox"
        ),
        None,
    )
    resize_handle = next(
        (
            item
            for item in descendants
            if item.objectName() == "desktopPetResizeHandle"
        ),
        None,
    )
    if accessory_box is None or resize_handle is None:
        raise RuntimeError("compact box drag targets did not load")

    # One exact production/emergency size per corner.  The pytest wrapper
    # repeats these logical layouts at 100%, 150% and 200% Qt scale, so each
    # corner covers both a distinct size and every target DPR.
    radial_cases = (
        (_area(-900, -500, 184, 175, "tiny-left-top"), "left", "top", 48.0),
        (_area(1600, -600, 223, 211, "small-right-top"), "right", "top", 59.0),
        (_area(-1000, 900, 275, 261, "compact-left-bottom"), "left", "bottom", 74.0),
        (_area(2200, 800, 401, 379, "normal-right-bottom"), "right", "bottom", 110.0),
    )
    radial_results: list[dict[str, object]] = []
    qt_scale = float(os.environ.get("QT_SCALE_FACTOR", "1") or 1)
    for area, horizontal_edge, vertical_edge, requested_size in radial_cases:
        area["devicePixelRatio"] = qt_scale
        backend.offscreen_work_areas = [area]
        fitted_size = float(root.fittedCompactSize(requested_size, area))
        root.setProperty("compactBoxSize", fitted_size)
        app.processEvents()
        pet_width = float(pet_window.width())
        pet_height = float(pet_window.height())
        x = (
            float(area["left"]) - pet_width * 0.30
            if horizontal_edge == "left"
            else float(area["right"]) - pet_width * 0.70
        )
        y = (
            float(area["top"]) + 8
            if vertical_edge == "top"
            else float(area["bottom"]) - pet_height - 8
        )
        pet_window.setProperty("geometryClampActive", True)
        pet_window.setX(round(x))
        pet_window.setY(round(y))
        pet_window.setProperty("geometryClampActive", False)
        compact_window.setProperty("expanded", True)
        QTest.qWait(720)
        app.processEvents()

        buttons: list[dict[str, object]] = []
        all_inside = True
        for item in action_items:
            origin = item.mapToItem(compact_window, QPointF(0, 0))
            global_left = float(pet_window.x()) + float(origin.x())
            global_top = float(pet_window.y()) + float(origin.y())
            global_right = global_left + float(item.width())
            global_bottom = global_top + float(item.height())
            inside = bool(
                _inside(global_left, float(area["left"]) + 8, float(area["right"]) - 8)
                and _inside(global_right, float(area["left"]) + 8, float(area["right"]) - 8)
                and _inside(global_top, float(area["top"]) + 8, float(area["bottom"]) - 8)
                and _inside(global_bottom, float(area["top"]) + 8, float(area["bottom"]) - 8)
            )
            all_inside = all_inside and inside
            buttons.append(
                {
                    "id": str(item.objectName()),
                    "rect": [
                        round(global_left, 2),
                        round(global_top, 2),
                        round(global_right, 2),
                        round(global_bottom, 2),
                    ],
                    "inside": inside,
                }
            )
        collisions = []
        for first, second in itertools.combinations(buttons, 2):
            overlap = _overlap_area(first["rect"], second["rect"])
            if overlap > 0.01:
                collisions.append(
                    {
                        "first": first["id"],
                        "second": second["id"],
                        "area": round(overlap, 3),
                    }
                )
        unique_centers = []
        for button in buttons:
            rect = button["rect"]
            center_x = (rect[0] + rect[2]) / 2.0
            center_y = (rect[1] + rect[3]) / 2.0
            containing = [
                candidate["id"]
                for candidate in buttons
                if candidate["rect"][0] <= center_x <= candidate["rect"][2]
                and candidate["rect"][1] <= center_y <= candidate["rect"][3]
            ]
            local_x = center_x - float(pet_window.x())
            local_y = center_y - float(pet_window.y())
            unique_centers.append(
                {
                    "id": button["id"],
                    "targets": containing,
                    "nativeHitAccepted": hit_test_filter.accepts_point(local_x, local_y),
                    "passed": containing == [button["id"]]
                    and hit_test_filter.accepts_point(local_x, local_y),
                }
            )
        no_collisions = not collisions
        unique_click_targets = all(item["passed"] for item in unique_centers)
        radial_results.append(
            {
                "area": area,
                "edge": f"{horizontal_edge}-{vertical_edge}",
                "effectiveSize": round(float(root.property("compactBoxSize")), 3),
                "windowRect": [
                    pet_window.x(),
                    pet_window.y(),
                    pet_window.x() + pet_window.width(),
                    pet_window.y() + pet_window.height(),
                ],
                "orbitProgress": round(float(compact_window.property("orbitProgress")), 4),
                "gridMode": bool(compact_window.property("actionGridMode")),
                "buttons": buttons,
                "collisions": collisions,
                "uniqueCenters": unique_centers,
                "passed": all_inside and no_collisions and unique_click_targets,
            }
        )

    # Verify native-mode threshold handoff with event-time global positions at
    # each Qt DPR. Offscreen refuses the actual system move; the one priming
    # transaction and request must still occur on the >4 px event, not a frame.
    backend.setPetDragMode("system")
    compact_window.setProperty("expanded", False)
    backend.offscreen_work_areas = [_area(0, 0, 960, 720, "threshold-screen")]
    root.setProperty("preferredCompactBoxSize", 110.0)
    root.setProperty("compactBoxSize", 110.0)
    pet_window.setProperty("geometryClampActive", True)
    pet_window.setPosition(QPoint(100, 80))
    pet_window.setProperty("geometryClampActive", False)
    press_x = float(pet_window.property("compactCharacterLeft")) + float(
        pet_window.property("compactCharacterWidth")) / 2.0
    press_y = float(pet_window.property("compactCharacterTop")) + float(
        pet_window.property("compactCharacterHeight")) * 0.45
    for kind, dx in ((QEvent.Type.MouseButtonPress, 0), (QEvent.Type.MouseMove, 3),
                     (QEvent.Type.MouseMove, 8)):
        event = QMouseEvent(
            kind, QPointF(press_x + dx, press_y),
            QPointF(100 + press_x + dx, 80 + press_y),
            Qt.MouseButton.LeftButton if dx == 0 else Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        backend.offscreen_cursor = {"x": 100 + press_x + dx, "y": 80 + press_y}
        delivered = not pointer_event_filter.eventFilter(pet_window, event)
        if dx == 0:
            pet_body.beginPointer(press_x, press_y)
        else:
            pet_body.movePointer(press_x + dx, press_y, True)
        if dx == 3:
            threshold_held = bool(
                delivered and not pet_window.property("dragMoved")
                and pointer_event_filter._root_system_move_attempts == 0
                and pet_window.x() == 100 and pet_window.y() == 80
            )
    native_threshold_event_time = {
        "qtScaleFactor": os.environ.get("QT_SCALE_FACTOR", "1"),
        "heldBelowThreshold": threshold_held,
        "windowAfterEvent": [pet_window.x(), pet_window.y()],
        "nativeAttempts": pointer_event_filter._root_system_move_attempts,
        "primingCommits": pointer_event_filter._direct_move_commits,
        "passed": bool(
            threshold_held and delivered and pet_window.property("dragMoved")
            and pet_window.x() == 108 and pet_window.y() == 80
            and pointer_event_filter._root_system_move_attempts == 1
            and pointer_event_filter._direct_move_commits == 1
        ),
    }
    pet_body.endPointer()
    app.processEvents()
    compact_window.setProperty("expanded", False)
    # This real gesture queues habitat finalization, a 220 ms persistence
    # debounce and a 340 ms presentation settle. Drain that terminal work
    # before constructing the next independent synthetic frame fixture.
    QTest.qWait(380)
    app.processEvents()
    assert not pet_window.property("manualDragActive")
    assert not pet_window.property("dragFinalizePending")
    # All following frame/coalescing tests explicitly exercise direct mode.
    backend.setPetDragMode("direct")

    # QMouseEvent/MultiPointTouchArea positions, QWindow x/y and QCursor.pos()
    # are all Qt logical coordinates.  Alternate the event-driven path with
    # the 16 ms global-cursor safety net after a cross-screen fit has resized
    # the window.  The held point must not oscillate when the next local event
    # is expressed relative to that newly moved/resized window.
    drag_area = {
        **_area(0, 0, 960, 540, "logical-200pct-drag"),
        "devicePixelRatio": 2.0,
    }
    backend.offscreen_work_areas = [drag_area]
    # Establish ownership before assigning exact origins; otherwise x/y
    # Behaviors can keep a prior scene glide alive in the first three samples.
    pet_window.setProperty("manualDragActive", True)
    pet_window.cancelPositionAnimations()
    pet_window.setProperty("lastCapturedPointerEventAt", 0)
    pet_window.setProperty("dragPointerEventPending", False)
    root.setProperty("preferredCompactBoxSize", 184.0)
    root.setProperty("compactBoxSize", 184.0)
    pet_window.setX(-820)
    pet_window.setY(40)
    pet_window.setProperty("dragGrabOffsetX", 140.0)
    pet_window.setProperty("dragGrabOffsetY", 100.0)
    pet_window.setProperty("dragStartCursorX", -680.0)
    pet_window.setProperty("dragStartCursorY", 140.0)
    pet_window.setProperty("dragMoved", True)
    cursor = {"x": 450, "y": 100}
    backend.offscreen_cursor = cursor
    pet_window.followGlobalPointer()
    app.processEvents()
    samples: list[list[float]] = []
    for _index in range(6):
        event_x = float(cursor["x"]) - float(pet_window.x())
        event_y = float(cursor["y"]) - float(pet_window.y())
        pet_window.followPointerEvent(event_x, event_y)
        app.processEvents()
        samples.append(
            [
                float(pet_window.x()),
                float(pet_window.y()),
                float(root.property("compactBoxSize")),
            ]
        )
        pet_window.followGlobalPointer()
        app.processEvents()
        samples.append(
            [
                float(pet_window.x()),
                float(pet_window.y()),
                float(root.property("compactBoxSize")),
            ]
        )
    reference = samples[0]
    drag_event_timer_handoff = {
        "area": drag_area,
        "cursor": cursor,
        "samples": samples,
        "grabOffset": [
            float(cursor["x"]) - float(pet_window.x()),
            float(cursor["y"]) - float(pet_window.y()),
        ],
        "passed": all(
            math.isclose(sample[0], reference[0], abs_tol=0.01)
            and math.isclose(sample[1], reference[1], abs_tol=0.01)
            and math.isclose(sample[2], reference[2], abs_tol=0.01)
            for sample in samples
        ),
    }
    pet_window.setProperty("manualDragActive", False)
    backend.offscreen_cursor = None

    # The 16 ms drag frame must consume an event captured by the native
    # QWindow filter even when MouseArea did not deliver onPositionChanged.
    # This is the real-world gap that otherwise makes the pet pause behind
    # the hand and then jump to the cursor-polling fallback.
    pet_window.setProperty("manualDragActive", True)
    pet_window.cancelPositionAnimations()
    root.setProperty("preferredCompactBoxSize", 110.0)
    root.setProperty("compactBoxSize", 110.0)
    pet_window.setX(100)
    pet_window.setY(80)
    pet_window.setProperty("dragGrabOffsetX", 60.0)
    pet_window.setProperty("dragGrabOffsetY", 50.0)
    pet_window.setProperty("dragStartCursorX", 160.0)
    pet_window.setProperty("dragStartCursorY", 130.0)
    pet_window.setProperty("dragMoved", True)
    captured_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(150.0, 110.0),
        QPointF(250.0, 190.0),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    serial_before = int(pointer_event_filter.serial)
    pointer_event_filter.eventFilter(pet_window, captured_event)
    pet_window.followPointerFrame()
    app.processEvents()
    serial_after = int(pointer_event_filter.serial)
    timer_captured_event = {
        "dragMode": backend.petDragMode,
        "serialBefore": serial_before,
        "serialAfter": serial_after,
        "consumedSerial": int(pet_window.property("consumedPointerEventSerial")),
        "window": [float(pet_window.x()), float(pet_window.y())],
        "expectedWindow": [190.0, 140.0],
        "passed": bool(
            serial_after > serial_before
            and int(pet_window.property("consumedPointerEventSerial"))
            == serial_after
            and math.isclose(float(pet_window.x()), 190.0, abs_tol=0.01)
            and math.isclose(float(pet_window.y()), 140.0, abs_tol=0.01)
        ),
    }
    pet_window.setProperty("manualDragActive", False)

    # Drive the complete MouseArea press/move/release chain.  Each move event
    # is created against the old window origin, then the cursor safety net is
    # forced to move the QWindow before that event is delivered.  This is the
    # worst-case compositor ordering that previously amplified every frame.
    pressure_areas = [
        {
            **_area(-1200, 0, 1200, 800, "pressure-125pct"),
            "devicePixelRatio": 1.25,
        },
        {
            **_area(0, 0, 960, 540, "pressure-200pct"),
            "devicePixelRatio": 2.0,
        },
    ]
    backend.offscreen_work_areas = pressure_areas
    root.setProperty("preferredCompactBoxSize", 184.0)
    root.setProperty("compactBoxSize", 184.0)
    pet_window.setProperty("geometryClampActive", True)
    pet_window.setX(-850)
    pet_window.setY(32)
    pet_window.setProperty("geometryClampActive", False)
    pet_window.cancelPositionAnimations()
    backend.detachPetHabitat(float(pet_window.x()), float(pet_window.y()))
    app.processEvents()
    press_point = QPointF(
        float(pet_window.property("compactCharacterLeft"))
        + float(pet_window.property("compactCharacterWidth")) / 2,
        float(pet_window.property("compactCharacterTop"))
        + float(pet_window.property("compactCharacterHeight")) * 0.45,
    )
    press_global = pet_window.mapToGlobal(press_point.toPoint())
    backend.offscreen_cursor = {"x": press_global.x(), "y": press_global.y()}
    pet_window.installEventFilter(pointer_event_filter)
    QTest.mousePress(
        pet_window,
        Qt.MouseButton.LeftButton,
        pos=press_point.toPoint(),
    )
    app.processEvents()
    active_after_press = bool(pet_window.property("manualDragActive"))
    forward_x = list(range(press_global.x() + 12, 661, 19))
    reverse_x = list(range(641, press_global.x() - 1, -23))
    pressure_samples: list[dict[str, object]] = []
    previous_cursor_x = press_global.x()
    for index, cursor_x in enumerate([*forward_x, *reverse_x]):
        cursor_y = press_global.y() + ((index % 9) - 4) * 2
        old_origin = [float(pet_window.x()), float(pet_window.y())]
        stale_local = QPointF(
            float(cursor_x) - old_origin[0],
            float(cursor_y) - old_origin[1],
        )
        delayed_event = QMouseEvent(
            QEvent.Type.MouseMove,
            stale_local,
            QPointF(float(cursor_x), float(cursor_y)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        backend.offscreen_cursor = {"x": cursor_x, "y": cursor_y}
        pet_window.followGlobalPointerNow()
        app.processEvents()
        after_fallback = [float(pet_window.x()), float(pet_window.y())]
        QApplication.sendEvent(pet_window, delayed_event)
        pet_window.followPointerFrame()
        app.processEvents()
        held_global = [
            float(pet_window.x())
            + float(pet_window.property("dragGrabOffsetX")),
            float(pet_window.y())
            + float(pet_window.property("dragGrabOffsetY")),
        ]
        pressure_samples.append(
            {
                "cursor": [cursor_x, cursor_y],
                "cursorStepX": cursor_x - previous_cursor_x,
                "oldOrigin": old_origin,
                "staleLocal": [stale_local.x(), stale_local.y()],
                "afterFallback": after_fallback,
                "afterEvent": [float(pet_window.x()), float(pet_window.y())],
                "heldError": [
                    held_global[0] - float(cursor_x),
                    held_global[1] - float(cursor_y),
                ],
                "effectiveSize": float(root.property("compactBoxSize")),
            }
        )
        previous_cursor_x = cursor_x
    release_cursor = pressure_samples[-1]["cursor"]
    release_local = QPointF(
        float(release_cursor[0]) - float(pet_window.x()),
        float(release_cursor[1]) - float(pet_window.y()),
    )
    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        release_local,
        QPointF(float(release_cursor[0]), float(release_cursor[1])),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(pet_window, release_event)
    QTest.qWait(30)
    pet_window.removeEventFilter(pointer_event_filter)
    qtest_drag_pressure = {
        "qtScaleFactor": os.environ.get("QT_SCALE_FACTOR", "1"),
        "windowDevicePixelRatio": float(pet_window.devicePixelRatio()),
        "activeAfterPress": active_after_press,
        "sampleCount": len(pressure_samples),
        "samples": pressure_samples,
        "released": not bool(pet_window.property("manualDragActive")),
        "passed": (
            active_after_press
            and len(pressure_samples) >= 70
            and all(
                abs(float(sample["heldError"][0])) <= 1.01
                and abs(float(sample["heldError"][1])) <= 1.01
                for sample in pressure_samples
            )
            and not bool(pet_window.property("manualDragActive"))
        ),
    }
    backend.offscreen_cursor = None

    # Reproduce the asynchronous-window-system failure described by Qt's
    # QMouseEvent contract.  A MouseArea move can carry local coordinates
    # calculated against the old native window origin.  If the 16 ms cursor
    # fallback has already moved the QWindow, reconstructing global position
    # as currentWindowX + staleLocalX overshoots and the two paths fight.
    root.setProperty("preferredCompactBoxSize", 110.0)
    root.setProperty("compactBoxSize", 110.0)
    pet_window.setX(100)
    pet_window.setY(80)
    pet_window.setProperty("dragGrabOffsetX", 60.0)
    pet_window.setProperty("dragGrabOffsetY", 50.0)
    pet_window.setProperty("dragStartCursorX", 160.0)
    pet_window.setProperty("dragStartCursorY", 130.0)
    pet_window.setProperty("dragMoved", True)
    pet_window.setProperty("manualDragActive", True)
    stale_event_results: list[dict[str, object]] = []
    for cursor_x, cursor_y in ((180, 142), (214, 167), (260, 191), (305, 225)):
        old_origin = [float(pet_window.x()), float(pet_window.y())]
        stale_local = [cursor_x - old_origin[0], cursor_y - old_origin[1]]
        backend.offscreen_cursor = {"x": cursor_x, "y": cursor_y}
        pet_window.followGlobalPointer()
        app.processEvents()
        expected = [
            float(cursor_x) - float(pet_window.property("dragGrabOffsetX")),
            float(cursor_y) - float(pet_window.property("dragGrabOffsetY")),
        ]
        after_fallback = [float(pet_window.x()), float(pet_window.y())]
        stale_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(stale_local[0], stale_local[1]),
            QPointF(cursor_x, cursor_y),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        pointer_event_filter.eventFilter(pet_window, stale_event)
        pet_window.followPointerEvent(stale_local[0], stale_local[1])
        pet_window.followPointerFrame()
        app.processEvents()
        after_stale_event = [float(pet_window.x()), float(pet_window.y())]
        stale_event_results.append(
            {
                "cursor": [cursor_x, cursor_y],
                "oldOrigin": old_origin,
                "staleLocal": stale_local,
                "expected": expected,
                "afterFallback": after_fallback,
                "afterStaleEvent": after_stale_event,
                "error": [
                    after_stale_event[0] - expected[0],
                    after_stale_event[1] - expected[1],
                ],
            }
        )
    stale_local_event_handoff = {
        "samples": stale_event_results,
        "passed": all(
            math.isclose(float(sample["afterStaleEvent"][0]), float(sample["expected"][0]), abs_tol=0.01)
            and math.isclose(float(sample["afterStaleEvent"][1]), float(sample["expected"][1]), abs_tol=0.01)
            for sample in stale_event_results
        )
        and math.isclose(
            float(stale_event_results[0]["afterFallback"][0]),
            float(stale_event_results[0]["expected"][0]),
            abs_tol=0.01,
        )
        and math.isclose(
            float(stale_event_results[0]["afterFallback"][1]),
            float(stale_event_results[0]["expected"][1]),
            abs_tol=0.01,
        ),
    }
    pet_window.setProperty("manualDragActive", False)
    backend.offscreen_cursor = None

    # Exercise the real PointerHandler chains, rather than calling their
    # backing resize/layout functions directly.  Both controls use logical
    # global coordinates; the same path therefore runs at every QT_SCALE_FACTOR
    # in the parametrized pytest wrapper and across a negative-origin seam.
    interaction_areas = [
        {
            **_area(-1200, 0, 1200, 900, "drag-controls-125pct"),
            "devicePixelRatio": 1.25,
        },
        {
            **_area(0, 0, 1400, 1000, "drag-controls-200pct"),
            "devicePixelRatio": 2.0,
        },
    ]
    backend.offscreen_work_areas = interaction_areas
    root.setProperty("preferredCompactBoxSize", 120.0)
    root.setProperty("compactBoxSize", 120.0)
    pet_window.setProperty("geometryClampActive", True)
    pet_window.setX(-360)
    pet_window.setY(180)
    pet_window.setProperty("geometryClampActive", False)
    backend.detachPetHabitat(float(pet_window.x()), float(pet_window.y()))
    compact_window.setProperty("expanded", False)
    # Start the accessory-follow probe away from the transparent window
    # edges.  Its persisted default intentionally sits beside Lilith and the
    # final +80/+40 sample can therefore reach the production boundary clamp
    # while the preceding compact-size polish is still settling at 200% DPI.
    # This probe measures PointerHandler follow, not boundary clamping, so
    # give the same gesture enough unobstructed travel at every scale factor.
    compact_window.setProperty("accessoryDx", 0.0)
    compact_window.setProperty("accessoryDy", 0.0)
    app.processEvents()

    accessory_press_local = _item_center_in_window(accessory_box, pet_window).toPoint()
    accessory_press_global = pet_window.mapToGlobal(accessory_press_local)
    accessory_start_center = _item_global_center(accessory_box, pet_window)
    backend.offscreen_cursor = {
        "x": accessory_press_global.x(),
        "y": accessory_press_global.y(),
    }
    QTest.mousePress(
        pet_window,
        Qt.MouseButton.LeftButton,
        pos=accessory_press_local,
    )
    app.processEvents()
    accessory_samples: list[dict[str, object]] = []
    for sample_index, (dx, dy) in enumerate(
        ((6, 3), (15, 8), (30, 16), (50, 25), (80, 40))
    ):
        target = QPoint(
            accessory_press_global.x() + dx,
            accessory_press_global.y() + dy,
        )
        backend.offscreen_cursor = {"x": target.x(), "y": target.y()}
        QTest.mouseMove(pet_window, pet_window.mapFromGlobal(target), 2)
        # Production coalesces high-polling pointer packets onto the Qt Quick
        # display clock.  The offscreen plugin may park that clock even after
        # qWait(), so request and synchronously render one real Quick frame
        # before measuring the visible control instead of expecting the
        # pre-v0.3.48 synchronous per-packet mutation.
        pet_window.requestUpdate()
        QTest.qWait(20)
        app.processEvents()
        pet_window.grabWindow()
        app.processEvents()
        center = (
            _item_global_center(accessory_box, pet_window)
            if sample_index == 0
            else _wait_for_rendered_item_center(
                accessory_box,
                pet_window,
                app,
                accessory_start_center[0] + dx,
                accessory_start_center[1] + dy,
            )
        )
        accessory_samples.append(
            {
                "pointerDelta": [dx, dy],
                "center": list(center),
                "error": [
                    center[0] - (accessory_start_center[0] + dx),
                    center[1] - (accessory_start_center[1] + dy),
                ],
            }
        )
    accessory_release = QPoint(
        accessory_press_global.x() + 80,
        accessory_press_global.y() + 40,
    )
    backend.offscreen_cursor = {
        "x": accessory_release.x(),
        "y": accessory_release.y(),
    }
    QTest.mouseRelease(
        pet_window,
        Qt.MouseButton.LeftButton,
        pos=pet_window.mapFromGlobal(accessory_release),
    )
    app.processEvents()

    root.setProperty("preferredCompactBoxSize", 120.0)
    root.setProperty("compactBoxSize", 120.0)
    pet_window.setProperty("geometryClampActive", True)
    pet_window.setX(-360)
    pet_window.setY(180)
    pet_window.setProperty("geometryClampActive", False)
    app.processEvents()
    resize_press_local = _item_center_in_window(resize_handle, pet_window).toPoint()
    resize_press_global = pet_window.mapToGlobal(resize_press_local)
    resize_start_center = _item_global_center(resize_handle, pet_window)
    backend.offscreen_cursor = {
        "x": resize_press_global.x(),
        "y": resize_press_global.y(),
    }
    # Make the production hover-dependent handle visible from a genuine
    # character hover before moving onto and pressing the control.
    resize_hover_armed = _reveal_resize_handle_from_character_hover(
        pet_window, resize_handle, app
    )
    QTest.mouseMove(pet_window, resize_press_local, 2)
    QTest.qWait(24)
    app.processEvents()
    QTest.mousePress(
        pet_window,
        Qt.MouseButton.LeftButton,
        pos=resize_press_local,
    )
    app.processEvents()
    resize_samples: list[dict[str, object]] = []
    saves_before_resize_release = len(backend.offscreen_box_layout_saves)
    for sample_index, (dx, dy) in enumerate(
        ((6, 6), (15, 15), (35, 28), (80, 50), (140, 80))
    ):
        target = QPoint(
            resize_press_global.x() + dx,
            resize_press_global.y() + dy,
        )
        backend.offscreen_cursor = {"x": target.x(), "y": target.y()}
        QTest.mouseMove(pet_window, pet_window.mapFromGlobal(target), 2)
        # Exercise the FrameAnimation coalescer itself. This remains much
        # slower than a real 60/120 Hz event source only because the verifier
        # records every injected sample rather than just the latest one.
        pet_window.requestUpdate()
        QTest.qWait(20)
        app.processEvents()
        pet_window.grabWindow()
        app.processEvents()
        center = (
            _item_global_center(resize_handle, pet_window)
            if sample_index == 0
            else _wait_for_rendered_item_center(
                resize_handle,
                pet_window,
                app,
                resize_start_center[0] + dx,
                resize_start_center[1] + dy,
            )
        )
        target_area = backend.screenWorkAreaAt(float(target.x()), float(target.y()))
        resize_samples.append(
            {
                "pointerDelta": [dx, dy],
                "center": list(center),
                "error": [
                    center[0] - (resize_start_center[0] + dx),
                    center[1] - (resize_start_center[1] + dy),
                ],
                "size": float(root.property("compactBoxSize")),
                "targetArea": str(target_area.get("name", "")),
                "targetDevicePixelRatio": float(
                    target_area.get("devicePixelRatio", 1.0)
                ),
            }
        )
    resize_release = QPoint(
        resize_press_global.x() + 140,
        resize_press_global.y() + 80,
    )
    backend.offscreen_cursor = {
        "x": resize_release.x(),
        "y": resize_release.y(),
    }
    QTest.mouseRelease(
        pet_window,
        Qt.MouseButton.LeftButton,
        pos=pet_window.mapFromGlobal(resize_release),
    )
    app.processEvents()
    resize_release_writes = (
        len(backend.offscreen_box_layout_saves) - saves_before_resize_release
    )
    pointer_handler_follow = {
        "qtScaleFactor": os.environ.get("QT_SCALE_FACTOR", "1"),
        "windowDevicePixelRatio": float(pet_window.devicePixelRatio()),
        "accessory": accessory_samples,
        "resize": resize_samples,
        "resizeHoverArmed": resize_hover_armed,
        "resizeReleased": not bool(pet_window.property("resizeDragActive")),
        "resizeReleaseWrites": resize_release_writes,
        "passed": bool(
            resize_hover_armed
            and all(
                abs(float(sample["error"][0])) <= 1.1
                and abs(float(sample["error"][1])) <= 1.1
                for sample in accessory_samples[1:]
            )
            and all(
                abs(float(sample["error"][0])) <= 1.1
                and abs(float(sample["error"][1])) <= 1.1
                for sample in resize_samples[1:]
            )
            and float(resize_samples[-1]["size"])
            > float(resize_samples[1]["size"])
            and not bool(pet_window.property("resizeDragActive"))
            and resize_release_writes == 1
        ),
    }
    backend.offscreen_cursor = None
    # Let any queued QWindow geometry/screen notifications from the held
    # cross-monitor resize drain before the verifier mutates unrelated helper
    # windows below.  Production ignores these callbacks while resizeDragActive
    # is set; the extra event turn also proves none re-clamps the released pet.
    QTest.qWait(320)
    app.processEvents()

    # Moving the pet onto a negative-coordinate work area must update the
    # integrated aura's placement-area binding without waiting for QScreen to
    # reassign its native window.
    negative_area = _area(-2560, 120, 960, 540, "mixed-dpi-negative")
    backend.offscreen_work_areas = [negative_area]
    pet_window.setProperty("geometryClampActive", True)
    pet_window.setX(-2140)
    pet_window.setY(180)
    pet_window.setProperty("geometryClampActive", False)
    QTest.qWait(35)
    bound_area_value = focus_aura.property("placementArea")
    if hasattr(bound_area_value, "toVariant"):
        bound_area_value = bound_area_value.toVariant()
    bound_area = dict(bound_area_value or {})
    integrated_binding = {
        "area": bound_area,
        "passed": (
            int(bound_area.get("left", 0)) == -2560
            and int(bound_area.get("top", 0)) == 120
            and int(bound_area.get("width", 0)) == 960
            and int(bound_area.get("height", 0)) == 540
        ),
    }

    aura_cases = (
        (
            _area(-2560, 120, 960, 540, "negative-secondary"),
            -1650.0,
            128.0,
            -1740.0,
            -1608.0,
            286.0,
        ),
        (
            _area(4000, -900, 190, 150, "small-logical"),
            4018.0,
            -894.0,
            4002.0,
            4064.0,
            -825.0,
        ),
        (
            _area(-420, 1400, 100, 88, "ultra-small-logical"),
            -370.0,
            1402.0,
            -402.0,
            -338.0,
            1444.0,
        ),
    )
    aura_results: list[dict[str, object]] = []
    focus_aura.setProperty("presentationEnabled", False)
    for area, anchor_x, anchor_y, subject_left, subject_right, subject_y in aura_cases:
        focus_aura.setProperty("placementArea", area)
        focus_aura.setProperty("anchorX", anchor_x)
        focus_aura.setProperty("anchorY", anchor_y)
        focus_aura.setProperty("subjectLeft", subject_left)
        focus_aura.setProperty("subjectRight", subject_right)
        focus_aura.setProperty("subjectCenterY", subject_y)
        QTest.qWait(20)
        app.processEvents()
        left = float(focus_aura.x())
        top = float(focus_aura.y())
        right = left + float(focus_aura.width())
        bottom = top + float(focus_aura.height())
        glow = focus_aura.findChild(QQuickItem, "focusTimerOrbitKnotGlow")
        knot = focus_aura.findChild(QQuickItem, "focusTimerOrbitKnot")
        sweep = focus_aura.findChild(QQuickItem, "focusTimerActivitySweep")
        round_markers = bool(
            glow is not None
            and knot is not None
            and sweep is not None
            and math.isclose(glow.width(), glow.height(), abs_tol=0.01)
            and math.isclose(knot.width(), knot.height(), abs_tol=0.01)
            and math.isclose(sweep.width(), sweep.height(), abs_tol=0.01)
        )
        inside = bool(
            _inside(left, float(area["left"]), float(area["right"]), 0.1)
            and _inside(right, float(area["left"]), float(area["right"]), 0.1)
            and _inside(top, float(area["top"]), float(area["bottom"]), 0.1)
            and _inside(bottom, float(area["top"]), float(area["bottom"]), 0.1)
        )
        aura_results.append(
            {
                "area": area,
                "windowRect": [left, top, right, bottom],
                "dialExtent": float(focus_aura.property("dialExtent")),
                "roundMarkers": round_markers,
                "passed": inside and round_markers,
            }
        )

    # PetHabitat remains a pure-function contract here: negative origins,
    # equivalent DPI-scaled geometry, and the smallest production window size
    # must preserve the contact anchor and vertical reachability.
    habitat_results: list[dict[str, object]] = []
    for dpi_scale in (1.0, 1.5, 2.0):
        work = WindowRect(
            round(-1280 * dpi_scale),
            round(300 * dpi_scale),
            round(-320 * dpi_scale),
            round(840 * dpi_scale),
        )
        host = WindowRect(
            round(-1040 * dpi_scale),
            round(380 * dpi_scale),
            round(-520 * dpi_scale),
            round(690 * dpi_scale),
        )
        candidate = choose_habitat_candidate(
            host,
            work,
            pet_width=385 * dpi_scale,
            pet_height=363 * dpi_scale,
            title_bar_height=32 * dpi_scale,
            dpi_scale=dpi_scale,
        )
        anchor_from_window = [
            candidate.x + 385 * dpi_scale * candidate.anchor_norm_x,
            candidate.y + 363 * dpi_scale * candidate.anchor_norm_y,
        ]
        contact_stable = bool(
            math.isclose(anchor_from_window[0], candidate.anchor_x, abs_tol=0.25)
            and math.isclose(anchor_from_window[1], candidate.anchor_y, abs_tol=0.25)
        )
        vertically_reachable = bool(
            candidate.y >= work.top - 0.1
            and candidate.y + 363 * dpi_scale <= work.bottom + 0.1
            and work.top <= candidate.anchor_y <= work.bottom
        )
        habitat_results.append(
            {
                "dpiScale": dpi_scale,
                "profile": candidate.profile,
                "position": [candidate.x, candidate.y],
                "anchor": [candidate.anchor_x, candidate.anchor_y],
                "contactStable": contact_stable,
                "verticallyReachable": vertically_reachable,
                "passed": contact_stable and vertically_reachable,
            }
        )

    passed = bool(
        all(bool(case["passed"]) for case in radial_results)
        and bool(native_threshold_event_time["passed"])
        and bool(drag_event_timer_handoff["passed"])
        and bool(timer_captured_event["passed"])
        and bool(stale_local_event_handoff["passed"])
        and bool(qtest_drag_pressure["passed"])
        and bool(pointer_handler_follow["passed"])
        and bool(integrated_binding["passed"])
        and all(bool(case["passed"]) for case in aura_results)
        and all(bool(case["passed"]) for case in habitat_results)
    )
    report = {
        "platform": os.environ["QT_QPA_PLATFORM"],
        "radialMenu": radial_results,
        "nativeThresholdEventTime": native_threshold_event_time,
        "dragEventTimerHandoff": drag_event_timer_handoff,
        "dragTimerCapturedEvent": timer_captured_event,
        "staleLocalEventHandoff": stale_local_event_handoff,
        "qtestDragPressure": qtest_drag_pressure,
        "pointerHandlerFollow": pointer_handler_follow,
        "focusAuraBinding": integrated_binding,
        "focusAura": aura_results,
        "petHabitat": habitat_results,
        "passed": passed,
    }
    report_path = PROJECT_ROOT / "artifacts" / "cross-dpi-layout-audit.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({**report, "report": str(report_path)}, ensure_ascii=False))

    compact_window.setProperty("expanded", False)
    backend.shutdown()
    engine.deleteLater()
    app.processEvents()
    temporary.cleanup()
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
