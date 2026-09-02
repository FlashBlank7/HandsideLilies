from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PET_QML = PROJECT_ROOT / "qml" / "V03PetBody.qml"


def _pet_source() -> str:
    return PET_QML.read_text(encoding="utf-8")


def test_paused_cord_geometry_sources_share_one_pending_scheduler() -> None:
    source = _pet_source()
    canvas_start = source.index("        id: supportCord")
    canvas_end = source.index("\n    Item {", canvas_start)
    cord = source[canvas_start:canvas_end]

    assert "property bool geometryUpdatePending: false" in cord
    assert "property bool geometryUpdateNeedsReset: false" in cord
    assert "function scheduleGeometryUpdate(resetRequired)" in cord
    assert "function flushScheduledGeometryUpdate()" in cord
    assert "Qt.callLater(flushScheduledGeometryUpdate)" in cord

    scheduler_start = cord.index("function scheduleGeometryUpdate(resetRequired)")
    scheduler_end = cord.index("function flushScheduledGeometryUpdate()", scheduler_start)
    scheduler = cord[scheduler_start:scheduler_end]
    assert scheduler.index("if (geometryUpdatePending)") < scheduler.index(
        "Qt.callLater(flushScheduledGeometryUpdate)"
    )
    assert "if (!root.paused)" in scheduler
    assert "resetCord()" in scheduler

    assert "onWidthChanged: scheduleGeometryUpdate(false)" in cord
    assert "onHeightChanged: scheduleGeometryUpdate(false)" in cord
    assert "Component.onCompleted: scheduleGeometryUpdate(true)" in cord
    assert "onCordStartChanged: supportCord.scheduleGeometryUpdate(false)" in source
    assert "onCordEndChanged: supportCord.scheduleGeometryUpdate(false)" in source
    assert "onCordNodeCountChanged: supportCord.scheduleGeometryUpdate(true)" in source
    assert "root.paused ? reflowCord() : resetCord()" not in source


def test_paused_cord_burst_commits_once_offscreen() -> None:
    qt_app = QGuiApplication.instance()
    assert qt_app is not None
    engine = QQmlApplicationEngine()
    qml = b"""
import QtQuick
import QtQuick.Window

Window {
    width: 360
    height: 460
    visible: false

    QtObject {
        id: backend
        property var habitatState: ({})
        property var themeManifest: ({"character": {}})
        property var wardrobeState: ({"current": {"outfit_id": "first-encounter"}})
        function assetUrl(key) { return "" }
    }

    V03PetBody {
        objectName: "cordCoalescingPet"
        width: 320
        height: 420
        appBackend: backend
        paused: true
    }
}
"""
    engine.loadData(
        qml,
        QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "CordCoalescingHarness.qml")),
    )
    assert engine.rootObjects(), "cord coalescing QML harness failed to load"
    window = engine.rootObjects()[0]
    pet = window.findChild(QObject, "cordCoalescingPet")
    cord = window.findChild(QObject, "desktopPetCordV03")
    assert pet is not None
    assert cord is not None

    # Drain construction-time bindings and the first queued geometry pass.
    QTest.qWait(20)
    qt_app.processEvents()
    baseline = int(cord.property("geometryCommitCount"))
    assert baseline == 1

    # All four setters run synchronously in one event turn.  No geometry work
    # is committed until Qt.callLater flushes the final endpoint/size state.
    pet.setProperty("cordStart", QPointF(92.0, 171.0))
    pet.setProperty("cordEnd", QPointF(287.0, 255.0))
    pet.setProperty("width", 336.0)
    pet.setProperty("height", 438.0)
    assert int(cord.property("geometryCommitCount")) == baseline
    assert cord.property("geometryUpdatePending") is True

    QTest.qWait(10)
    qt_app.processEvents()
    assert int(cord.property("geometryCommitCount")) == baseline + 1
    assert cord.property("geometryUpdatePending") is False

    # A reset request dominates ordinary reflow requests, but the mixed burst
    # still commits only once.
    baseline = int(cord.property("geometryCommitCount"))
    pet.setProperty("width", 342.0)
    pet.setProperty("cordNodeCount", 16)
    pet.setProperty("cordEnd", QPointF(301.0, 264.0))
    assert int(cord.property("geometryCommitCount")) == baseline
    QTest.qWait(10)
    qt_app.processEvents()
    assert int(cord.property("geometryCommitCount")) == baseline + 1
    assert len(cord.property("nodes").toVariant()) == 16

    # Live animation retains the old immediate-reset contract; the deferred
    # paused scheduler never inserts a late reset into the Verlet clock.
    pet.setProperty("paused", False)
    baseline = int(cord.property("geometryCommitCount"))
    pet.setProperty("cordStart", QPointF(96.0, 176.0))
    assert int(cord.property("geometryCommitCount")) == baseline + 1
    assert cord.property("geometryUpdatePending") is False

    engine.deleteLater()
