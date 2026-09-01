from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

# This verifier must never create a surface on the user's desktop.  Keep the
# guard here as well as in the pytest launcher so direct/manual invocation is
# just as safe as the automated regression.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QObject, QPoint, Slot
from PySide6.QtQml import QJSValue, QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import lilies.backend as backend_module
from lilies.paths import qml_path
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts


class FakeNativeMoveController(QObject):
    """Deterministic QML bridge with an optional start-time cancel re-entry."""

    def __init__(self, pet_window: QQuickWindow, pet_body: QQuickItem) -> None:
        super().__init__(pet_window)
        self.pet_window = pet_window
        self.pet_body = pet_body
        self.cancel_synchronously = False
        self.start_result = True
        self.start_calls: list[int] = []
        self.acknowledged: list[int] = []
        self.reentrant_snapshot: dict[str, object] = {}

    @Slot(int, result=bool)
    def tryStartSystemMove(self, gesture_serial: int) -> bool:
        serial = int(gesture_serial)
        self.start_calls.append(serial)
        if self.cancel_synchronously:
            # QWindow::startSystemMove can release the MouseArea grab before
            # returning.  Emit the production QML cancel signal on this exact
            # stack to reproduce that ordering without entering a native move.
            self.pet_body.characterCanceled.emit(False)
            self.reentrant_snapshot = {
                "startPending": bool(
                    self.pet_window.property("nativeSystemMoveStartPending")
                ),
                "manualDragActive": bool(
                    self.pet_window.property("manualDragActive")
                ),
                "nativeMoveActive": bool(
                    self.pet_window.property("nativeSystemMoveActive")
                ),
                "cancelPending": bool(
                    self.pet_window.property("nativeSystemMoveCancelPending")
                ),
                "gestureSerial": int(
                    self.pet_window.property("nativeSystemMoveGestureSerial")
                ),
            }
        return self.start_result

    @Slot(int)
    def acknowledgeSystemMoveFinished(self, gesture_serial: int) -> None:
        self.acknowledged.append(int(gesture_serial))


class _SyntheticCursor:
    points: list[QPoint] = []
    samples = 0

    @classmethod
    def pos(cls) -> QPoint:
        if not cls.points:
            raise AssertionError("synthetic cursor was sampled unexpectedly")
        point = cls.points[min(cls.samples, len(cls.points) - 1)]
        cls.samples += 1
        return QPoint(point)


def _figure_bounds(pet_window: QQuickWindow) -> dict[str, float]:
    left = float(pet_window.x()) + float(
        pet_window.property("compactCharacterLeft")
    )
    top = float(pet_window.y()) + float(
        pet_window.property("compactCharacterTop")
    )
    width = float(pet_window.property("compactCharacterWidth"))
    height = float(pet_window.property("compactCharacterHeight"))
    return {
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
        "width": width,
        "height": height,
    }


def _character_point(pet_window: QQuickWindow) -> tuple[float, float]:
    return (
        float(pet_window.property("compactCharacterLeft"))
        + float(pet_window.property("compactCharacterWidth")) / 2.0,
        float(pet_window.property("compactCharacterTop"))
        + float(pet_window.property("compactCharacterHeight")) * 0.45,
    )


def _wait_for_attached_window_settle(
    pet_window: QQuickWindow,
    backend: OffscreenBackend,
    *,
    timeout_ms: int = 1_500,
) -> dict[str, object]:
    """Wait for the habitat glide to reach its authoritative window target.

    A fixed wall-clock wait is not sufficient on Qt's offscreen QPA: its
    animation clock can advance while the final Quick polish/render is still
    coalesced.  Starting the local-pose gesture in that last 1--2 px of the
    *window* glide makes the release-side habitat replay look like pose-local
    drift.  Require the real production target for three rendered turns so
    this scenario isolates item-local motion as intended.
    """

    deadline = time.monotonic() + max(0.1, timeout_ms / 1_000.0)
    stable_turns = 0
    samples = 0
    target = [float("nan"), float("nan")]
    native_target = [float("nan"), float("nan")]
    current = [float(pet_window.x()), float(pet_window.y())]
    while time.monotonic() < deadline:
        habitat = dict(backend.habitatState or {})
        target = [
            float(habitat.get("x", pet_window.x())),
            float(habitat.get("y", pet_window.y())),
        ]
        # QML's Window x/y ultimately map to QWindow integer coordinates.
        # The habitat controller intentionally keeps subpixel geometry for
        # anchor calculations, while the native top-level position truncates
        # that value at the platform boundary (for example 94.67 -> 94).
        native_target = [float(math.trunc(target[0])), float(math.trunc(target[1]))]
        current = [float(pet_window.x()), float(pet_window.y())]
        samples += 1
        if (
            abs(current[0] - native_target[0]) <= 0.01
            and abs(current[1] - native_target[1]) <= 0.01
        ):
            stable_turns += 1
            if stable_turns >= 3:
                return {
                    "settled": True,
                    "target": target,
                    "nativeTarget": native_target,
                    "position": current,
                    "samples": samples,
                }
        else:
            stable_turns = 0
        # A real exposed window continuously receives compositor frames.
        # Explicitly render one on the offscreen platform before yielding to
        # the animation driver, otherwise the final interpolation sample may
        # remain parked until the synthetic press itself requests a frame.
        pet_window.requestUpdate()
        pet_window.grabWindow()
        QApplication.processEvents()
        QTest.qWait(16)
    return {
        "settled": False,
        "target": target,
        "nativeTarget": native_target,
        "position": current,
        "samples": samples,
    }


def _qml_point(value: object) -> tuple[float, float]:
    if isinstance(value, QJSValue):
        value = value.toVariant()
    if isinstance(value, dict):
        return float(value["x"]), float(value["y"])
    x_member = getattr(value, "x", None)
    y_member = getattr(value, "y", None)
    if x_member is None or y_member is None:
        raise TypeError(f"QML value is not a point: {value!r}")
    return (
        float(x_member() if callable(x_member) else x_member),
        float(y_member() if callable(y_member) else y_member),
    )


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="lilies-drag-v0325-")
    os.environ["LILIES_DATA_DIR"] = temporary.name
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    load_windows_ui_fonts()
    backend = OffscreenBackend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    backend._shell_monitor.stop()
    # ``smoke=True`` keeps every external/native provider inert during
    # construction.  The branches under test are intentionally the normal
    # (non-preview) drag and avoidance branches, backed only by the fakes in
    # this verifier.
    backend._preview_mode = False
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(str(qml_path()))

    outcome: dict[str, object] = {}
    try:
        if not engine.rootObjects():
            raise RuntimeError("Main.qml failed to load in the offscreen verifier")
        root = engine.rootObjects()[0]
        pet_window = root.findChild(QQuickWindow, "petWindow")
        pet_body = root.findChild(QQuickItem, "compactLilith")
        compact_window = root.findChild(QQuickItem, "desktopPet")
        if pet_window is None or pet_body is None or compact_window is None:
            raise RuntimeError("compact drag runtime objects were not created")

        fake_native = FakeNativeMoveController(pet_window, pet_body)
        if not pet_window.setProperty("nativeMoveController", fake_native):
            raise RuntimeError("fake native move controller was not installed")

        work_area = {
            "left": 0,
            "top": 0,
            "right": 800,
            "bottom": 600,
            "width": 800,
            "height": 600,
            "name": "synthetic-drag-screen",
            "devicePixelRatio": 1.0,
        }
        backend.offscreen_work_areas = [work_area]
        root.setProperty("preferredCompactBoxSize", 120.0)
        root.setProperty("compactBoxSize", 120.0)
        pet_window.setX(180)
        pet_window.setY(80)
        backend.detachPetHabitat(float(pet_window.x()), float(pet_window.y()))
        backend.clearPetInteractionLocks()
        compact_window.setProperty("expanded", False)
        QApplication.processEvents()

        # Direct is the persisted default. The exact 4 px boundary remains a
        # click; higher-rate samples are staged and the drag frame consumes
        # only the newest point before moving the QWindow.
        point_x, point_y = _character_point(pet_window)
        start_x = float(pet_window.x())
        start_y = float(pet_window.y())
        cursor_x = start_x + point_x
        cursor_y = start_y + point_y
        backend.offscreen_cursor = {"x": round(cursor_x), "y": round(cursor_y)}
        pet_body.beginPointer(point_x, point_y)
        pressed_state = {
            "mode": str(backend.petDragMode),
            "nativeCalls": len(fake_native.start_calls),
            "manualDragActive": bool(pet_window.property("manualDragActive")),
        }
        backend.offscreen_cursor = {
            "x": round(cursor_x + 4.0),
            "y": round(cursor_y),
        }
        pet_body.movePointer(point_x + 4.0, point_y, True)
        QApplication.processEvents()
        pet_window.followPointerFrame()
        QApplication.processEvents()
        at_threshold = {
            "dragMoved": bool(pet_window.property("dragMoved")),
            "delta": [float(pet_window.x()) - start_x, float(pet_window.y()) - start_y],
        }
        backend.offscreen_cursor = {
            "x": round(cursor_x + 20.0),
            "y": round(cursor_y),
        }
        pet_body.movePointer(point_x + 20.0, point_y, True)
        QApplication.processEvents()
        pet_window.followPointerFrame()
        QApplication.processEvents()
        after_threshold = {
            "dragMoved": bool(pet_window.property("dragMoved")),
            "delta": [float(pet_window.x()) - start_x, float(pet_window.y()) - start_y],
        }
        pet_body.endPointer()
        QApplication.processEvents()
        outcome["directThresholdAndBridge"] = {
            "pressed": pressed_state,
            "atFourPixels": at_threshold,
            "afterTwentyPixels": after_threshold,
            "nativeCalls": len(fake_native.start_calls),
            "interactionSnapAfterRelease": bool(
                pet_body.property("interactionSnap")
            ),
            "passed": (
                pressed_state["mode"] == "direct"
                and pressed_state["nativeCalls"] == 0
                and pressed_state["manualDragActive"] is True
                and at_threshold["dragMoved"] is False
                and abs(at_threshold["delta"][0]) <= 0.01
                and abs(at_threshold["delta"][1]) <= 0.01
                and after_threshold["dragMoved"] is True
                and abs(after_threshold["delta"][0] - 20.0) <= 2.0
                and abs(after_threshold["delta"][1]) <= 2.0
                and len(fake_native.start_calls) == 0
                and not bool(pet_window.property("manualDragActive"))
                and not bool(pet_body.property("interactionSnap"))
            ),
        }

        # Start from the real maximized-window edge habitat so release begins
        # a pose transition.  The delayed production clamp must use the final
        # standing figure, leave that figure recoverable, and avoid forcing the
        # much larger transparent QWindow wholly on screen.
        backend.pet_habitat.stable_seconds = 0.0
        backend.pet_habitat.update_foreground(
            {
                "handle": 32025,
                "rect": {
                    "left": work_area["left"],
                    "top": work_area["top"],
                    "right": work_area["right"],
                    "bottom": work_area["bottom"],
                },
                "workArea": dict(work_area),
                "visible": True,
                "minimized": False,
                "maximized": True,
            },
            now=time.monotonic() - 1.0,
        )
        backend._habitat_status = backend.pet_habitat.status()
        backend.habitatChanged.emit()
        QTest.qWait(360)
        attached_pose = str(backend.habitatState.get("pose", ""))
        point_x, point_y = _character_point(pet_window)
        cursor_x = float(pet_window.x()) + point_x
        cursor_y = float(pet_window.y()) + point_y
        backend.offscreen_cursor = {"x": round(cursor_x), "y": round(cursor_y)}
        pet_body.beginPointer(point_x, point_y)
        edge_cursor_x = float(work_area["right"]) - 2.0
        edge_cursor_y = float(work_area["bottom"]) - 2.0
        backend.offscreen_cursor = {
            "x": round(edge_cursor_x),
            "y": round(edge_cursor_y),
        }
        pet_body.movePointer(
            point_x + edge_cursor_x - cursor_x,
            point_y + edge_cursor_y - cursor_y,
            True,
        )
        QApplication.processEvents()
        pet_window.followPointerFrame()
        QApplication.processEvents()
        grab_norm = [
            float(pet_window.property("dragCharacterGrabNormX")),
            float(pet_window.property("dragCharacterGrabNormY")),
        ]
        mapped_grab = _qml_point(
            pet_body.characterPointForNormalizedGrab(*grab_norm)
        )
        held_global = [
            float(pet_window.x()) + mapped_grab[0],
            float(pet_window.y()) + mapped_grab[1],
        ]
        grab_error = math.hypot(
            held_global[0] - edge_cursor_x,
            held_global[1] - edge_cursor_y,
        )
        grab_remapped = bool(
            pet_window.property("dragCharacterGrabRemapped")
        )
        pet_body.endPointer()
        QTest.qWait(390)
        final_figure = _figure_bounds(pet_window)
        visible_width = max(
            0.0,
            min(final_figure["right"], float(work_area["right"]))
            - max(final_figure["left"], float(work_area["left"])),
        )
        visible_height = max(
            0.0,
            min(final_figure["bottom"], float(work_area["bottom"]))
            - max(final_figure["top"], float(work_area["top"])),
        )
        window_outside = (
            float(pet_window.x()) < float(work_area["left"])
            or float(pet_window.y()) < float(work_area["top"])
            or float(pet_window.x()) + float(pet_window.width())
            > float(work_area["right"])
            or float(pet_window.y()) + float(pet_window.height())
            > float(work_area["bottom"])
        )
        outcome["directNearEdgeRelease"] = {
            "attachedPose": attached_pose,
            "finalFigure": final_figure,
            "visibleFigure": [visible_width, visible_height],
            "window": [
                float(pet_window.x()),
                float(pet_window.y()),
                float(pet_window.width()),
                float(pet_window.height()),
            ],
            "windowOutside": window_outside,
            "nativeCalls": len(fake_native.start_calls),
            "habitatPosition": [
                float(backend.pet_habitat.desktop_x),
                float(backend.pet_habitat.desktop_y),
            ],
            "interactionSnapAfterSettle": bool(
                pet_body.property("interactionSnap")
            ),
            "passed": (
                attached_pose.startswith("edge-peek")
                and visible_width >= min(56.0, final_figure["width"]) - 1.0
                and visible_height >= min(72.0, final_figure["height"]) - 1.0
                and window_outside
                and len(fake_native.start_calls) == 0
                and not bool(backend.habitatState.get("attached"))
                and not bool(pet_body.property("interactionSnap"))
                and abs(backend.pet_habitat.desktop_x - float(pet_window.x())) <= 1.0
                and abs(backend.pet_habitat.desktop_y - float(pet_window.y())) <= 1.0
            ),
        }
        outcome["characterGrabContinuity"] = {
            "attachedPose": attached_pose,
            "normalizedGrab": grab_norm,
            "heldGlobal": held_global,
            "cursorGlobal": [edge_cursor_x, edge_cursor_y],
            "errorPixels": grab_error,
            "remapped": grab_remapped,
            # The release clears the transient remap flag, so continuity is
            # judged from the held frame sampled immediately before it.
            "passed": (
                attached_pose.startswith("edge-peek")
                and grab_remapped
                and grab_error <= 2.0
            ),
        }

        # Item-local motion is not authoritative: a pose/frame change can
        # report a large local delta while the native event-time cursor has
        # not moved at all.  This must remain a click and must not detach.
        backend.pet_habitat.update_foreground(
            {
                "handle": 42025,
                "appId": "wps.exe",
                "processId": 42025,
                "rect": {
                    "left": 120,
                    "top": 210,
                    "right": 680,
                    "bottom": 540,
                },
                "workArea": dict(work_area),
                "visible": True,
                "minimized": False,
                "maximized": False,
            },
            now=time.monotonic() - 1.0,
        )
        backend._habitat_status = backend.pet_habitat.status()
        backend.habitatChanged.emit()
        habitat_settle = _wait_for_attached_window_settle(
            pet_window, backend
        )
        backend.setPetDragMode("direct")
        compact_window.setProperty("expanded", False)
        point_x, point_y = _character_point(pet_window)
        stationary_global = [
            float(pet_window.x()) + point_x,
            float(pet_window.y()) + point_y,
        ]
        backend.offscreen_cursor = {
            "x": stationary_global[0],
            "y": stationary_global[1],
        }
        event_serial = int(
            pet_window.property("capturedPointerEventSerial")
        ) + 1
        pet_window.setProperty("capturedPointerGlobalX", stationary_global[0])
        pet_window.setProperty("capturedPointerGlobalY", stationary_global[1])
        pet_window.setProperty("capturedPointerEventSerial", event_serial)
        press_origin = [float(pet_window.x()), float(pet_window.y())]
        pet_body.beginPointer(point_x, point_y)
        pet_window.setProperty("capturedPointerEventSerial", event_serial + 1)
        pet_body.movePointer(point_x + 36.0, point_y, True)
        QApplication.processEvents()
        pet_window.followPointerFrame()
        QApplication.processEvents()
        local_reported_move = bool(pet_body.property("pointerMoved"))
        drag_latched = bool(pet_window.property("dragMoved"))
        pet_body.endPointer()
        QApplication.processEvents()
        stationary_after = [float(pet_window.x()), float(pet_window.y())]
        outcome["localPoseMotionDoesNotBecomeDrag"] = {
            "qmlReportedMove": local_reported_move,
            "globalDragLatched": drag_latched,
            "windowDelta": [
                stationary_after[0] - press_origin[0],
                stationary_after[1] - press_origin[1],
            ],
            "habitatState": str(backend.habitatState.get("state", "")),
            "menuOpenedAsClick": bool(compact_window.property("expanded")),
            "preGestureHabitatSettle": habitat_settle,
            "passed": (
                bool(habitat_settle["settled"])
                and
                local_reported_move
                and not drag_latched
                and math.hypot(
                    stationary_after[0] - press_origin[0],
                    stationary_after[1] - press_origin[1],
                ) <= 0.01
                and bool(backend.habitatState.get("attached"))
                and bool(compact_window.property("expanded"))
            ),
        }
        compact_window.setProperty("expanded", False)

        # A settled centre-screen drag has exactly one persistence authority:
        # release.  The 340 ms pose-settle pass may clamp and save if geometry
        # truly changed, but must not rewrite an identical layout.
        pet_window.setProperty("geometryClampActive", True)
        pet_window.setX(170)
        pet_window.setY(90)
        pet_window.setProperty("geometryClampActive", False)
        QApplication.processEvents()
        point_x, point_y = _character_point(pet_window)
        cursor_x = float(pet_window.x()) + point_x
        cursor_y = float(pet_window.y()) + point_y
        backend.offscreen_cursor = {"x": cursor_x, "y": cursor_y}
        saves_before_drag = len(backend.offscreen_box_layout_saves)
        pet_body.beginPointer(point_x, point_y)
        backend.offscreen_cursor = {"x": cursor_x + 28.0, "y": cursor_y + 12.0}
        pet_body.movePointer(point_x + 28.0, point_y + 12.0, True)
        QApplication.processEvents()
        pet_window.followPointerFrame()
        QApplication.processEvents()
        pet_body.endPointer()
        QApplication.processEvents()
        saves_after_release = len(backend.offscreen_box_layout_saves)
        position_after_release = [float(pet_window.x()), float(pet_window.y())]
        QTest.qWait(390)
        saves_after_settle = len(backend.offscreen_box_layout_saves)
        position_after_settle = [float(pet_window.x()), float(pet_window.y())]
        outcome["singlePersistenceWithoutSettleMovement"] = {
            "writesAtRelease": saves_after_release - saves_before_drag,
            "writesAfterSettle": saves_after_settle - saves_before_drag,
            "positionAtRelease": position_after_release,
            "positionAfterSettle": position_after_settle,
            "passed": (
                saves_after_release - saves_before_drag == 0
                and saves_after_settle - saves_before_drag == 1
                and math.hypot(
                    position_after_settle[0] - position_after_release[0],
                    position_after_settle[1] - position_after_release[1],
                ) <= 0.01
            ),
        }

        # Presence may become SILENT/BLOCKED while a held drag is in flight.
        # Hiding the QWindow is a cancellation boundary, but it must commit the
        # current window position without sampling a pointer inside the newly
        # protected/full-screen application.  Otherwise this process looks
        # correct until the next launch restores the stale saved layout.
        backend.setPetDragMode("direct")
        backend.pet_habitat.set_presence("normal")
        backend._habitat_status = backend.pet_habitat.status()
        backend.habitatChanged.emit()
        pet_window.setProperty("geometryClampActive", True)
        pet_window.setX(180)
        pet_window.setY(80)
        pet_window.setProperty("geometryClampActive", False)
        backend.detachPetHabitat(180.0, 80.0)
        backend.saveBoxLayout(180.0, 80.0, 120.0)
        QApplication.processEvents()
        point_x, point_y = _character_point(pet_window)
        cursor_x = float(pet_window.x()) + point_x
        cursor_y = float(pet_window.y()) + point_y
        backend.offscreen_cursor = {"x": cursor_x, "y": cursor_y}
        saves_before_hide = len(backend.offscreen_box_layout_saves)
        pet_body.beginPointer(point_x, point_y)
        backend.offscreen_cursor = {"x": cursor_x + 40.0, "y": cursor_y}
        pet_body.movePointer(point_x + 40.0, point_y, True)
        QApplication.processEvents()
        pet_window.followPointerFrame()
        QApplication.processEvents()
        position_before_hide = [float(pet_window.x()), float(pet_window.y())]
        drag_before_hide = bool(pet_window.property("manualDragActive"))
        backend.pet_habitat.set_presence("silent")
        backend._habitat_status = backend.pet_habitat.status()
        backend.habitatChanged.emit()
        QApplication.processEvents()
        saved_while_hidden = dict(backend.boxLayout())
        habitat_while_hidden = backend.pet_habitat.status()
        hidden_state = {
            "visible": bool(pet_window.isVisible()),
            "dragActive": bool(pet_window.property("manualDragActive")),
            "saved": [
                float(saved_while_hidden.get("x", 0.0)),
                float(saved_while_hidden.get("y", 0.0)),
            ],
            "habitat": [
                float(habitat_while_hidden.get("x", 0.0)),
                float(habitat_while_hidden.get("y", 0.0)),
            ],
            "writes": len(backend.offscreen_box_layout_saves) - saves_before_hide,
        }
        backend.pet_habitat.set_presence("normal")
        backend._habitat_status = backend.pet_habitat.status()
        backend.habitatChanged.emit()
        QTest.qWait(80)
        restored_position = [float(pet_window.x()), float(pet_window.y())]
        restored_habitat = backend.pet_habitat.status()
        outcome["interruptedDragCommitsBeforeHide"] = {
            "dragBeforeHide": drag_before_hide,
            "positionBeforeHide": position_before_hide,
            "hidden": hidden_state,
            "positionAfterRestore": restored_position,
            "habitatAfterRestore": [
                float(restored_habitat.get("x", 0.0)),
                float(restored_habitat.get("y", 0.0)),
            ],
            "attachedAfterRestore": bool(restored_habitat.get("attached")),
            "passed": (
                drag_before_hide
                and position_before_hide[0] >= 218.0
                and hidden_state["visible"] is False
                and hidden_state["dragActive"] is False
                and hidden_state["writes"] == 1
                and math.hypot(
                    hidden_state["saved"][0] - position_before_hide[0],
                    hidden_state["saved"][1] - position_before_hide[1],
                ) <= 2.0
                and math.hypot(
                    hidden_state["habitat"][0] - position_before_hide[0],
                    hidden_state["habitat"][1] - position_before_hide[1],
                ) <= 2.0
                and math.hypot(
                    restored_position[0] - hidden_state["saved"][0],
                    restored_position[1] - hidden_state["saved"][1],
                ) <= 2.0
                and math.hypot(
                    float(restored_habitat.get("x", 0.0))
                    - hidden_state["saved"][0],
                    float(restored_habitat.get("y", 0.0))
                    - hidden_state["saved"][1],
                ) <= 2.0
                and not bool(restored_habitat.get("attached"))
            ),
        }

        # A queued screenChanged clamp can outlive the gesture that superseded
        # it.  The monotonic gesture counter invalidates that callback even if
        # release has already reset the active serial to zero before it runs.
        pet_window.setProperty("geometryClampActive", True)
        pet_window.setX(-260)
        pet_window.setY(-180)
        pet_window.setProperty("geometryClampActive", False)
        pet_window.cancelPositionAnimations()
        stale_position = [float(pet_window.x()), float(pet_window.y())]
        gesture_counter = int(
            pet_window.property("nativeSystemMoveGestureCounter")
        )
        pet_window.scheduleScreenConstraint()
        pet_window.setProperty(
            "nativeSystemMoveGestureCounter", gesture_counter + 1
        )
        QApplication.processEvents()
        after_stale_constraint = [float(pet_window.x()), float(pet_window.y())]
        outcome["staleScreenConstraintIgnored"] = {
            "before": stale_position,
            "after": after_stale_constraint,
            "gestureCounterChanged": int(
                pet_window.property("nativeSystemMoveGestureCounter")
            ) == gesture_counter + 1,
            "passed": (
                math.hypot(
                    after_stale_constraint[0] - stale_position[0],
                    after_stale_constraint[1] - stale_position[1],
                ) <= 0.01
            ),
        }
        pet_window.setProperty("geometryClampActive", True)
        pet_window.setX(180)
        pet_window.setY(80)
        pet_window.setProperty("geometryClampActive", False)

        # A cancel emitted inside tryStartSystemMove must see the start-pending
        # guard and defer completion.  The outer frame then owns the same serial
        # and interaction lock until WM_EXITSIZEMOVE-style completion arrives.
        backend.setPetDragMode("system")
        backend.clearPetInteractionLocks()
        compact_window.setProperty("expanded", False)
        fake_native.cancel_synchronously = True
        point_x, point_y = _character_point(pet_window)
        backend.offscreen_cursor = {
            "x": round(float(pet_window.x()) + point_x),
            "y": round(float(pet_window.y()) + point_y),
        }
        calls_before_reentry = len(fake_native.start_calls)
        pet_body.beginPointer(point_x, point_y)
        reentrant_serial = int(
            pet_window.property("nativeSystemMoveGestureSerial")
        )
        after_reentry = {
            "manualDragActive": bool(pet_window.property("manualDragActive")),
            "nativeMoveActive": bool(
                pet_window.property("nativeSystemMoveActive")
            ),
            "startPending": bool(
                pet_window.property("nativeSystemMoveStartPending")
            ),
            "cancelPending": bool(
                pet_window.property("nativeSystemMoveCancelPending")
            ),
            "gestureSerial": reentrant_serial,
            "lockReasons": sorted(backend._pet_interaction_lock_reasons),
        }
        outcome["nativeStartCancelReentry"] = {
            "insideController": dict(fake_native.reentrant_snapshot),
            "afterStartReturns": after_reentry,
        }
        acknowledgements_before_finish = len(fake_native.acknowledged)
        wrong_serial_finish = bool(
            pet_window.finishNativeSystemMove(reentrant_serial + 1)
        )
        after_wrong_serial = {
            "manualDragActive": bool(pet_window.property("manualDragActive")),
            "nativeMoveActive": bool(
                pet_window.property("nativeSystemMoveActive")
            ),
            "gestureSerial": int(
                pet_window.property("nativeSystemMoveGestureSerial")
            ),
            "lockReasons": sorted(backend._pet_interaction_lock_reasons),
        }
        correct_serial_finish = bool(
            pet_window.finishNativeSystemMove(reentrant_serial)
        )
        QApplication.processEvents()
        after_correct_serial = {
            "manualDragActive": bool(pet_window.property("manualDragActive")),
            "nativeMoveActive": bool(
                pet_window.property("nativeSystemMoveActive")
            ),
            "startPending": bool(
                pet_window.property("nativeSystemMoveStartPending")
            ),
            "cancelPending": bool(
                pet_window.property("nativeSystemMoveCancelPending")
            ),
            "gestureSerial": int(
                pet_window.property("nativeSystemMoveGestureSerial")
            ),
            "lockReasons": sorted(backend._pet_interaction_lock_reasons),
            "acknowledgements": fake_native.acknowledged[
                acknowledgements_before_finish:
            ],
        }
        outcome["nativeStartCancelReentry"].update(
            {
                "wrongSerialFinish": wrong_serial_finish,
                "afterWrongSerial": after_wrong_serial,
                "correctSerialFinish": correct_serial_finish,
                "afterCorrectSerial": after_correct_serial,
                "passed": (
                len(fake_native.start_calls) == calls_before_reentry + 1
                and fake_native.reentrant_snapshot.get("startPending") is True
                and fake_native.reentrant_snapshot.get("manualDragActive") is True
                and fake_native.reentrant_snapshot.get("nativeMoveActive") is False
                and fake_native.reentrant_snapshot.get("cancelPending") is True
                and fake_native.reentrant_snapshot.get("gestureSerial")
                == reentrant_serial
                and after_reentry["manualDragActive"] is True
                and after_reentry["nativeMoveActive"] is True
                and after_reentry["startPending"] is False
                and after_reentry["cancelPending"] is True
                and after_reentry["gestureSerial"] > 0
                and after_reentry["lockReasons"] == ["character"]
                and wrong_serial_finish is False
                and after_wrong_serial["manualDragActive"] is True
                and after_wrong_serial["nativeMoveActive"] is True
                and after_wrong_serial["gestureSerial"] == reentrant_serial
                and after_wrong_serial["lockReasons"] == ["character"]
                and correct_serial_finish is True
                and after_correct_serial["manualDragActive"] is False
                and after_correct_serial["nativeMoveActive"] is False
                and after_correct_serial["startPending"] is False
                and after_correct_serial["cancelPending"] is False
                and after_correct_serial["gestureSerial"] == 0
                # A stationary native completion intentionally opens the
                # radial menu, which may own its independent ``menu`` lock.
                # Only this gesture's ``character`` lock must be gone.
                and "character" not in after_correct_serial["lockReasons"]
                and after_correct_serial["acknowledgements"]
                == [reentrant_serial]
                ),
            }
        )
        compact_window.setProperty("expanded", False)

        # Exercise both geometry notifications.  Once either axis crosses the
        # threshold, returning the native window to its press origin must not
        # turn the gesture back into a click/menu toggle.
        fake_native.cancel_synchronously = False
        axis_results: dict[str, object] = {}
        for axis in ("x", "y"):
            compact_window.setProperty("expanded", False)
            pet_window.setX(190)
            pet_window.setY(90)
            QApplication.processEvents()
            point_x, point_y = _character_point(pet_window)
            backend.offscreen_cursor = {
                "x": round(float(pet_window.x()) + point_x),
                "y": round(float(pet_window.y()) + point_y),
            }
            pet_body.beginPointer(point_x, point_y)
            serial = int(pet_window.property("nativeSystemMoveGestureSerial"))
            origin = float(pet_window.x() if axis == "x" else pet_window.y())
            if axis == "x":
                pet_window.setX(round(origin + 5.0))
            else:
                pet_window.setY(round(origin + 5.0))
            QApplication.processEvents()
            latched_out = bool(pet_window.property("dragMoved"))
            if axis == "x":
                pet_window.setX(round(origin))
            else:
                pet_window.setY(round(origin))
            QApplication.processEvents()
            latched_back = bool(pet_window.property("dragMoved"))
            returned = abs(
                float(pet_window.x() if axis == "x" else pet_window.y()) - origin
            ) <= 0.01
            before_manual_follow = [
                float(pet_window.x()),
                float(pet_window.y()),
            ]
            pet_window.followPointerAt(
                before_manual_follow[0] + point_x + 80.0,
                before_manual_follow[1] + point_y + 70.0,
            )
            after_manual_follow = [
                float(pet_window.x()),
                float(pet_window.y()),
            ]
            pet_window.finishNativeSystemMove(serial)
            QApplication.processEvents()
            axis_results[axis] = {
                "latchedOutbound": latched_out,
                "latchedAtOrigin": latched_back,
                "returnedToOrigin": returned,
                "manualFollowSuppressed": after_manual_follow
                == before_manual_follow,
                "menuStayedClosed": not bool(compact_window.property("expanded")),
                "gestureFinished": not bool(
                    pet_window.property("manualDragActive")
                ),
            }
        outcome["nativeOutAndBackLatch"] = {
            "axes": axis_results,
            "passed": all(
                value["latchedOutbound"]
                and value["latchedAtOrigin"]
                and value["returnedToOrigin"]
                and value["manualFollowSuppressed"]
                and value["menuStayedClosed"]
                and value["gestureFinished"]
                for value in axis_results.values()
            ),
        }

        # Unsupported compositor moves must fall back to the same event-time
        # direct path.  The failed bridge is attempted once on press, clears
        # the native serial, and never prevents the >4 px move or its cleanup.
        fake_native.start_result = False
        compact_window.setProperty("expanded", False)
        pet_window.setX(210)
        pet_window.setY(100)
        QApplication.processEvents()
        point_x, point_y = _character_point(pet_window)
        fallback_start_x = float(pet_window.x())
        fallback_start_y = float(pet_window.y())
        cursor_x = fallback_start_x + point_x
        cursor_y = fallback_start_y + point_y
        backend.offscreen_cursor = {"x": round(cursor_x), "y": round(cursor_y)}
        calls_before_fallback = len(fake_native.start_calls)
        pet_body.beginPointer(point_x, point_y)
        fallback_after_press = {
            "manualDragActive": bool(pet_window.property("manualDragActive")),
            "nativeMoveAttempted": bool(
                pet_window.property("nativeSystemMoveAttempted")
            ),
            "nativeMoveActive": bool(
                pet_window.property("nativeSystemMoveActive")
            ),
            "gestureSerial": int(
                pet_window.property("nativeSystemMoveGestureSerial")
            ),
        }
        backend.offscreen_cursor = {
            "x": round(cursor_x + 20.0),
            "y": round(cursor_y),
        }
        pet_body.movePointer(point_x + 20.0, point_y, True)
        QApplication.processEvents()
        pet_window.followPointerFrame()
        QApplication.processEvents()
        fallback_during_move = {
            "dragMoved": bool(pet_window.property("dragMoved")),
            "manualDragActive": bool(pet_window.property("manualDragActive")),
            "delta": [
                float(pet_window.x()) - fallback_start_x,
                float(pet_window.y()) - fallback_start_y,
            ],
            "nativeCalls": len(fake_native.start_calls) - calls_before_fallback,
        }
        pet_body.endPointer()
        QApplication.processEvents()
        fallback_after_release = {
            "manualDragActive": bool(pet_window.property("manualDragActive")),
            "nativeMoveAttempted": bool(
                pet_window.property("nativeSystemMoveAttempted")
            ),
            "gestureSerial": int(
                pet_window.property("nativeSystemMoveGestureSerial")
            ),
            "lockReasons": sorted(backend._pet_interaction_lock_reasons),
        }
        outcome["failedNativeStartFallsBackDirect"] = {
            "afterPress": fallback_after_press,
            "duringMove": fallback_during_move,
            "afterRelease": fallback_after_release,
            "passed": (
                fallback_after_press["manualDragActive"] is True
                and fallback_after_press["nativeMoveAttempted"] is True
                and fallback_after_press["nativeMoveActive"] is False
                and fallback_after_press["gestureSerial"] == 0
                and fallback_during_move["dragMoved"] is True
                and fallback_during_move["manualDragActive"] is True
                and abs(fallback_during_move["delta"][0] - 20.0) <= 2.0
                and abs(fallback_during_move["delta"][1]) <= 2.0
                and fallback_during_move["nativeCalls"] == 1
                and fallback_after_release["manualDragActive"] is False
                and fallback_after_release["nativeMoveAttempted"] is False
                and fallback_after_release["gestureSerial"] == 0
                and fallback_after_release["lockReasons"] == []
            ),
        }
        fake_native.start_result = True

        # Drive the production avoidance pump with a synthetic cursor.  Two
        # named locks must aggregate; after the last unlock, the full 0.8 s
        # grace suppresses even an otherwise eligible fast approach.
        backend.clearPetInteractionLocks()
        backend.setPetAvoidanceMode("lively")
        backend.pet_habitat.update_foreground(None)
        backend._habitat_status = backend.pet_habitat.status()
        backend._pet_geometry = {
            "windowX": 200.0,
            "windowY": 100.0,
            "windowWidth": 420.0,
            "windowHeight": 396.0,
            "figureLeft": 300.0,
            "figureTop": 200.0,
            "figureWidth": 120.0,
            "figureHeight": 200.0,
            "workLeft": 0.0,
            "workTop": 0.0,
            "workWidth": 1000.0,
            "workHeight": 800.0,
            "menuOpen": False,
            "pointerDown": False,
            "visible": True,
        }
        backend._pet_avoidance_cooldown_until = 0.0
        avoidance_submissions: list[dict[str, object]] = []
        original_set_avoidance = backend.pet_habitat.set_avoidance_position
        original_cursor = backend_module.QCursor

        def record_avoidance(x, y, area, *, now=None):
            avoidance_submissions.append({"x": x, "y": y, "now": now})
            return True

        try:
            backend.pet_habitat.set_avoidance_position = record_avoidance
            backend_module.QCursor = _SyntheticCursor
            _SyntheticCursor.points = [QPoint(520, 300), QPoint(470, 300)]
            _SyntheticCursor.samples = 0
            backend.setPetInteractionLock("character", True)
            backend.setPetInteractionLock("menu", True)
            backend._pump_pet_avoidance(time.monotonic())
            backend.setPetInteractionLock("character", False)
            backend._pump_pet_avoidance(time.monotonic())
            locked_submissions = len(avoidance_submissions)
            locked_samples = _SyntheticCursor.samples
            locked_reasons = sorted(backend._pet_interaction_lock_reasons)
            backend.setPetInteractionLock("menu", False)
            grace_until = float(backend._pet_interaction_grace_until)
            backend._pump_pet_avoidance(grace_until - 0.001)
            grace_submissions = len(avoidance_submissions)
            grace_samples = _SyntheticCursor.samples
            backend._pump_pet_avoidance(grace_until + 0.01)
            backend._pump_pet_avoidance(grace_until + 0.11)
        finally:
            backend.pet_habitat.set_avoidance_position = original_set_avoidance
            backend_module.QCursor = original_cursor
        outcome["avoidanceInteractionGuard"] = {
            "remainingNamedLock": locked_reasons,
            "lockedSubmissions": locked_submissions,
            "lockedCursorSamples": locked_samples,
            "graceSubmissions": grace_submissions,
            "graceCursorSamples": grace_samples,
            "eligibleSubmissionsAfterGrace": len(avoidance_submissions),
            "passed": (
                locked_reasons == ["menu"]
                and locked_submissions == 0
                and locked_samples == 0
                and grace_submissions == 0
                and grace_samples == 0
                and len(avoidance_submissions) == 1
                and _SyntheticCursor.samples == 2
            ),
        }

        passed = all(
            bool(value.get("passed"))
            for value in outcome.values()
            if isinstance(value, dict)
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    finally:
        backend.shutdown()
        QApplication.processEvents()
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
