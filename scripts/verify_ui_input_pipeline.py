"""Exercise the real QML drag queue without frames, native input or screenshots."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                  QT_QUICK_CONTROLS_STYLE="Basic", QT_QPA_OFFSCREEN_SIZE="2560x1600")

from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from verify_drag_runtime_v0325 import FakeNativeMoveController, _character_point
from lilies.paths import qml_path


def main() -> None:
    parent = ROOT / "artifacts" / "ui-input-pipeline"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as data:
        os.environ["LILIES_DATA_DIR"] = data
        app = QApplication([])
        load_windows_ui_fonts()
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._shell_monitor.stop()
        engine = QQmlApplicationEngine()
        errors = []
        engine.warnings.connect(lambda warnings: errors.extend(w.toString() for w in warnings))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(str(qml_path()))
        try:
            root = engine.rootObjects()[0]
            pet = root.findChild(QQuickWindow, "petWindow")
            body = root.findChild(QQuickItem, "compactLilith")
            frame = pet.findChild(QObject, "dragFallbackFrameDriver")
            bridge = FakeNativeMoveController(pet, body, backend)
            pet.setProperty("nativeMoveController", bridge)
            backend.offscreen_work_areas = [dict(left=0, top=0, right=2560, bottom=1600,
                width=2560, height=1600, name="synthetic", devicePixelRatio=1.0)]
            backend.setPetDragMode("direct")
            backend.detachPetHabitat(300.0, 200.0)
            QApplication.processEvents()
            results = []
            for release_before_flush in (False, True, False):
                backend.clearPetInteractionLocks()
                point_x, point_y = _character_point(pet)
                start = [float(pet.x()), float(pet.y())]
                backend.offscreen_cursor = dict(x=start[0] + point_x, y=start[1] + point_y)
                body.beginPointer(point_x, point_y)
                # Prove that geometry no longer waits for a rendered frame.
                frame.setProperty("running", False)
                before = len(bridge.move_calls)
                for offset in (8.0, 16.0, 32.0):
                    pet.followPointerEvent(point_x + offset, point_y + 8.0)
                backend.offscreen_cursor = dict(x=start[0] + point_x + 32.0,
                    y=start[1] + point_y + 8.0)
                assert len(bridge.move_calls) == before, "mouse burst was not coalesced"
                if release_before_flush:
                    body.endPointer()
                QTest.qWait(25)
                assert len(bridge.move_calls) == before + 1, dict(
                    message="latest point must commit exactly once", releaseBeforeFlush=release_before_flush,
                    before=before, calls=bridge.move_calls, requests=bridge.move_requests,
                    pending=pet.property("pointerFlushQueued"), active=pet.property("manualDragActive"))
                assert abs(pet.x() - start[0] - 32.0) <= 1.0
                assert abs(pet.y() - start[1] - 8.0) <= 1.0
                assert not frame.property("running")
                if not release_before_flush:
                    body.endPointer()
                committed = len(bridge.move_calls)
                QApplication.processEvents()
                assert len(bridge.move_calls) == committed, "stale callback moved released pet"
                assert not pet.property("manualDragActive")
                results.append(dict(releaseBeforeFlush=release_before_flush, burst=3, commits=1))
            assert not errors, errors[:3]
            print(json.dumps(dict(passed=True, framesRequired=False, gestures=results)))
        finally:
            for window in app.topLevelWindows():
                window.hide()
            backend.shutdown()
            QApplication.processEvents()


if __name__ == "__main__":
    main()
