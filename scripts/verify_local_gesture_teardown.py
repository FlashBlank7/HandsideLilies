"""Owned offscreen QML input only; never touch native input or real data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QML_DISABLE_DISK_CACHE"] = "1"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from lilies.app import CompactHitTestFilter, configure_quick_window_resource_lifecycle
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject, QPoint, QPointF, Qt, QUrl, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

ROOT = Path(__file__).resolve().parents[1]
MODES = ("absent", "decline", "accept")
ACTIONS = ("release", "rebuild", "unpin", "reorder", "unpin-overlap", "hide", "unpin-repin")


class GestureBackend(OffscreenBackend):
    def __init__(self, *args, **kwargs):
        self.component_saves = []
        super().__init__(*args, **kwargs)

    @Slot(str, float, float, float)
    def saveComponentLayout(self, action, x, y, scale):
        self.component_saves.append({"action": action, "x": x, "y": y, "scale": scale})
        super().saveComponentLayout(action, x, y, scale)


class NativeGestureStub(QObject):
    def __init__(self, owner, accepted):
        super().__init__(owner)
        self.accepted = accepted
        self.begins = []
        self.ends = []

    @Slot(str, result=bool)
    def beginLocalGesture(self, surface):
        self.begins.append(surface)
        return self.accepted

    @Slot(str, bool, float, float, float, result=bool)
    def endLocalGestureWithCounts(self, surface, cancelled, raw, scene, native):
        self.ends.append({"surface": surface, "cancelled": cancelled})
        return True


def wait(predicate, label, seconds=4.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError(label)


def qml_call(obj, name, *args):
    return QMetaObject.invokeMethod(
        obj, name, Q_RETURN_ARG("QVariant"),
        *(Q_ARG("QVariant", value) for value in args),
    )


def run_case(app, data, native_mode, action):
    os.environ["LILIES_DATA_DIR"] = str(data)
    backend = GestureBackend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    backend._productivity_timer.stop()
    backend.selection.set_enabled(False)
    backend.offscreen_cursor = {"x": 250, "y": 250}
    backend.offscreen_work_areas = [
        {"left": 0, "top": 0, "right": 1920, "bottom": 1040,
         "width": 1920, "height": 1040}
    ]
    backend.setQuickActionPinned("focus", True)
    if action == "reorder":
        backend.setQuickActionPinned("reading", True)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(ROOT / "qml/Main.qml")))
    pet = None
    result = {"nativeMode": native_mode, "action": action, "warnings": warnings}
    try:
        assert engine.rootObjects(), warnings
        root = engine.rootObjects()[0]
        pet = root.findChild(QQuickWindow, "petWindow")
        compact = root.findChild(QQuickItem, "desktopPet")
        assert pet is not None and compact is not None
        controller = None
        if native_mode != "absent":
            controller = NativeGestureStub(pet, native_mode == "accept")
            pet.setProperty("nativeMoveController", controller)
        lifecycle = configure_quick_window_resource_lifecycle(root, backend)
        pet.setPosition(QPoint(200, 120))
        compact.setProperty("expanded", True)
        wait(lambda: compact.property("orbitProgress") > 0.999, "radial menu did not settle")
        assert not compact.property("actionGridMode"), "fixture must exercise draggable radial mode"
        def find_button():
            return next((item for item in CompactHitTestFilter._visual_descendants(pet.contentItem())
                         if item.objectName() == "desktopPetAction_focus"), None)

        def press_and_drag(item):
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            QTest.mousePress(pet, Qt.MouseButton.RightButton, pos=point)
            QTest.mouseMove(pet, point + QPoint(16, 0), delay=20)
            QTest.mouseMove(pet, point + QPoint(34, 8), delay=20)
            wait(lambda: "component-focus" in backend._pet_interaction_lock_reasons,
                 "offscreen right-button drag did not acquire the real QML handler")
            return point

        button = find_button()
        assert button is not None and button.isVisible()
        point = press_and_drag(button)

        def snapshot():
            return {
                "localDepth": int(pet.property("localGestureDepth")),
                "pointerLocked": backend.pet_pointer_interaction_active,
                "owners": sorted(backend._pet_interaction_lock_reasons),
                "streamPaused": bool(backend._chat_timeline._stream_paused),
                "cleanupPaused": lifecycle._pointer_interaction_active,
                "oldButtonAlive": isValid(button),
                "begins": list(controller.begins) if controller else [],
                "ends": list(controller.ends) if controller else [],
                "saves": list(backend.component_saves),
            }

        other_claimed = False
        if action == "unpin-overlap":
            # An unrelated owner must survive delegate removal. It is a
            # synthetic second owner, not a second OS pointer injection.
            backend.setPetInteractionLock("resize", True)
            other_claimed = bool(qml_call(pet, "beginLocalGesture", "resize"))
        result["held"] = snapshot()
        if action == "rebuild":
            backend.quickActionsChanged.emit()
        elif action.startswith("unpin"):
            backend.setQuickActionPinned("focus", False)
        elif action == "reorder":
            backend.moveQuickAction("focus", 1)
        elif action == "hide":
            pet.hide()
        elif action == "release":
            QTest.mouseRelease(pet, Qt.MouseButton.RightButton, pos=point + QPoint(34, 8))
        QTest.qWait(100)
        result["interrupted"] = snapshot()
        if action == "unpin-overlap":
            qml_call(pet, "endLocalGesture", "resize", other_claimed, False, 0, 0, 0)
            backend.setPetInteractionLock("resize", False)
        if action != "release":
            QTest.mouseRelease(pet, Qt.MouseButton.RightButton, pos=point + QPoint(34, 8))
        QTest.qWait(100)
        if action == "unpin-repin":
            assert not backend.pet_pointer_interaction_active
            backend.setQuickActionPinned("focus", True)
            QTest.qWait(100)
            replacement = find_button()
            assert replacement is not None and replacement is not button
            point = press_and_drag(replacement)
            result["replacementHeld"] = snapshot()
            QTest.mouseRelease(pet, Qt.MouseButton.RightButton, pos=point + QPoint(34, 8))
            QTest.qWait(100)
        result["released"] = snapshot()
        state = result["released"]
        # The observed Qt Repeater destroys delegates when order changes;
        # reordering is therefore a cancellation, not a normal saved release.
        removed = action.startswith("unpin") or action == "reorder"
        interrupted = result["interrupted"]
        expected_ends = 2 if action in {"unpin-repin", "unpin-overlap"} else 1
        result["checks"] = {
            "acquired_actual_handler": result["held"]["localDepth"] == (2 if action == "unpin-overlap" else 1)
            and result["held"]["pointerLocked"],
            "normal_release_persists_original_action_once": (
                len(state["saves"]) == 1 and state["saves"][0]["action"] == "focus"
                if action in {"release", "rebuild", "unpin-repin"} else True
            ),
            "removed_gesture_is_cancelled_without_saving": (
                not interrupted["oldButtonAlive"]
                and interrupted["localDepth"] == (1 if action == "unpin-overlap" else 0)
                and "component-focus" not in interrupted["owners"] and not interrupted["saves"]
                if removed else True
            ),
            "other_owner_survives_until_its_release": (
                interrupted["owners"] == ["menu", "resize"]
                and interrupted["pointerLocked"] and interrupted["streamPaused"]
                and interrupted["localDepth"] == 1
                if action == "unpin-overlap" else True
            ),
            "only_claimed_native_gesture_finishes_once": (
                len(state["ends"]) == expected_ends
                and (not removed or state["ends"][0]["cancelled"])
                if native_mode == "accept" else not state["ends"]
            ),
        }
        result["passed"] = (
            state["localDepth"] == 0 and not state["pointerLocked"]
            and not state["streamPaused"] and not state["cleanupPaused"]
            and not warnings and all(result["checks"].values())
        )
    finally:
        if pet is not None and isValid(pet):
            pet.hide()
        for window in app.topLevelWindows():
            window.hide()
        engine.deleteLater()
        QTest.qWait(40)
        backend.shutdown()
        backend.deleteLater()
        app.processEvents()
    result["passed"] = result["passed"] and not warnings
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = Path(args.report).resolve()
    if not report.is_relative_to(ROOT / "artifacts") or report.exists():
        raise ValueError("Use a new report beneath the project's artifacts directory")
    report.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    load_windows_ui_fonts()
    results = []
    with tempfile.TemporaryDirectory(prefix="gesture-data-", dir=report.parent) as temp:
        try:
            for mode in MODES:
                for action in ACTIONS:
                    result = run_case(app, Path(temp) / f"{mode}-{action}", mode, action)
                    results.append(result)
                    print(json.dumps({
                        "nativeMode": mode, "action": action,
                        "released": result["released"],
                        "warnings": len(result["warnings"]), "passed": result["passed"],
                    }, ensure_ascii=True), flush=True)
        finally:
            passed = len(results) == len(MODES) * len(ACTIONS) and all(row["passed"] for row in results)
            report.write_text(json.dumps({
                "passed": passed, "results": results,
                "scope": "Production QML with synthetic offscreen input/native stub and F data; no OS input, models, pixels or user windows",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    assert passed, "local gesture teardown left active state; see report"


if __name__ == "__main__":
    main()
