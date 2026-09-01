from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_QPA_OFFSCREEN_SIZE", "1200x900")

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.app import CompactHitTestFilter
from lilies.backend import Backend
from lilies.paths import qml_path


def _center_in_window(item: QQuickItem) -> QPoint:
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(point.x()), round(point.y()))


def _action(pet_window: QQuickWindow, action_id: str) -> QQuickItem | None:
    return next(
        (
            item
            for item in CompactHitTestFilter._visual_descendants(
                pet_window.contentItem()
            )
            if item.objectName() == f"desktopPetAction_{action_id}"
        ),
        None,
    )


def _named_item(window: QQuickWindow, object_name: str) -> QQuickItem | None:
    return next(
        (
            item
            for item in CompactHitTestFilter._visual_descendants(window.contentItem())
            if item.objectName() == object_name
        ),
        None,
    )


def _click_world_from_radial_menu(
    pet_window: QQuickWindow,
    accessory: QQuickItem,
    hit_test: CompactHitTestFilter,
) -> dict[str, object]:
    QTest.mouseClick(
        pet_window,
        Qt.MouseButton.LeftButton,
        pos=_center_in_window(accessory),
    )
    QTest.qWait(700)
    world_action = _action(pet_window, "world")
    if world_action is None:
        return {"actionFound": False, "nativeHit": False, "clicked": False}
    point = _center_in_window(world_action)
    result = {
        "actionFound": True,
        "actionVisible": bool(world_action.isVisible()),
        "actionEnabled": bool(world_action.isEnabled()),
        "nativeHit": bool(hit_test.accepts_point(point.x(), point.y())),
        "clickPoint": [point.x(), point.y()],
    }
    QTest.mouseClick(
        pet_window,
        Qt.MouseButton.LeftButton,
        pos=point,
    )
    QTest.qWait(260)
    result["clicked"] = True
    return result


def _visible_overlap(window: QQuickWindow, screen_geometry) -> tuple[int, int]:
    geometry = window.geometry()
    return (
        max(
            0,
            min(geometry.right(), screen_geometry.right())
            - max(geometry.left(), screen_geometry.left()),
        ),
        max(
            0,
            min(geometry.bottom(), screen_geometry.bottom())
            - max(geometry.top(), screen_geometry.top()),
        ),
    )


def _run_verification(app: QApplication, backend: Backend) -> bool:
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml failed to load")

    root = engine.rootObjects()[0]
    pet_window = root.findChild(QQuickWindow, "petWindow")
    accessory = root.findChild(QQuickItem, "compactAccessoryBox")
    world_scene = root.findChild(QQuickWindow, "boxWorldSceneWindow")
    work_panel = root.findChild(QQuickWindow, "v03WorkPanel")
    if (
        pet_window is None
        or accessory is None
        or world_scene is None
        or work_panel is None
    ):
        raise RuntimeError("compact box-world presentation windows failed to load")

    hit_test = CompactHitTestFilter(
        pet_window,
        backend,
        native_window_id=int(pet_window.winId()),
    )
    backend.setWorkPanelOpen(False)
    backend.setBoxWorldSceneOpen(False)
    QTest.qWait(500)

    first_click = _click_world_from_radial_menu(pet_window, accessory, hit_test)
    screen_geometry = world_scene.screen().availableGeometry()
    scene_geometry = world_scene.geometry()
    stage = _named_item(world_scene, "boxWorldSceneStage")
    manage_action = _named_item(world_scene, "boxWorldManageDecorationsButton")
    exit_action = _named_item(world_scene, "boxWorldSceneExitButton")
    first_presentation_count = int(world_scene.property("presentationCount") or 0)
    first_presentation = {
        **first_click,
        "compactRootHidden": not bool(root.isVisible()),
        "entered": bool(backend.boxWorldStatus.get("entered")),
        "backendOpen": bool(backend.boxWorldSceneOpen),
        "workPanelClosed": not bool(backend.workPanelOpen),
        "sceneVisible": bool(world_scene.isVisible()),
        "sceneExposed": bool(world_scene.isExposed()),
        "requestedVisible": bool(world_scene.property("requestedVisible")),
        "sceneTitle": str(world_scene.title()),
        "sceneOnTop": bool(
            world_scene.flags() & Qt.WindowType.WindowStaysOnTopHint
        ),
        "stageFound": stage is not None,
        "manageActionFound": manage_action is not None,
        "exitActionFound": exit_action is not None,
        "presentationCount": first_presentation_count,
        "screenSize": [screen_geometry.width(), screen_geometry.height()],
        "sceneSize": [scene_geometry.width(), scene_geometry.height()],
        "sceneFitsLogicalScreen": (
            scene_geometry.width() <= screen_geometry.width()
            and scene_geometry.height() <= screen_geometry.height()
        ),
        "narrowViewport": bool(world_scene.property("narrowViewport")),
    }

    # The retained scene stays logically open while minimized. Repeating the
    # same world action must call its presentation path again, restore the
    # native QWindow and repair a coordinate from a removed monitor.
    world_scene.setX(screen_geometry.right() + 400)
    world_scene.setY(screen_geometry.bottom() + 300)
    world_scene.showMinimized()
    QTest.qWait(140)
    minimized_before = world_scene.visibility() == QWindow.Visibility.Minimized
    second_click = _click_world_from_radial_menu(pet_window, accessory, hit_test)
    overlap_width, overlap_height = _visible_overlap(world_scene, screen_geometry)
    restored_geometry = world_scene.geometry()
    second_presentation = {
        **second_click,
        "minimizedBefore": minimized_before,
        "restoredFromMinimized": (
            world_scene.visibility() != QWindow.Visibility.Minimized
        ),
        "backendOpen": bool(backend.boxWorldSceneOpen),
        "sceneVisible": bool(world_scene.isVisible()),
        "sceneExposed": bool(world_scene.isExposed()),
        "sceneTitle": str(world_scene.title()),
        "presentationCountAdvanced": (
            int(world_scene.property("presentationCount") or 0)
            > first_presentation_count
        ),
        "restoredIntoPetWorkArea": (
            overlap_width >= min(160, restored_geometry.width() * 0.30)
            and overlap_height >= min(100, restored_geometry.height() * 0.24)
        ),
    }

    # Use the scene's own explicit close control. It must close both the QML
    # presentation and the backend state, without opening the old WorkPanel.
    exit_action = _named_item(world_scene, "boxWorldSceneExitButton")
    explicit_close_clicked = False
    if (
        exit_action is not None
        and second_presentation["restoredFromMinimized"]
        and second_presentation["sceneVisible"]
    ):
        QTest.mouseClick(
            world_scene,
            Qt.MouseButton.LeftButton,
            pos=_center_in_window(exit_action),
        )
        explicit_close_clicked = True
        QTest.qWait(160)
    closed = {
        "exitActionFound": exit_action is not None,
        "explicitCloseClicked": explicit_close_clicked,
        "sceneHidden": not bool(world_scene.isVisible()),
        "backendClosed": not bool(backend.boxWorldSceneOpen),
        "workPanelStillClosed": not bool(backend.workPanelOpen),
    }

    # Always leave the verifier's backend in a closed state even if a failed
    # restoration made the explicit button unreachable.
    backend.setBoxWorldSceneOpen(False)
    backend.setWorkPanelOpen(False)
    QTest.qWait(40)

    outcome = {
        "firstPresentation": first_presentation,
        "repeatAfterMinimize": second_presentation,
        "closed": closed,
    }
    print(json.dumps(outcome, ensure_ascii=False, indent=2))

    first_ok = all(
        (
            first_presentation["actionFound"],
            first_presentation["actionVisible"],
            first_presentation["actionEnabled"],
            first_presentation["nativeHit"],
            first_presentation["compactRootHidden"],
            first_presentation["entered"],
            first_presentation["backendOpen"],
            first_presentation["workPanelClosed"],
            first_presentation["sceneVisible"],
            first_presentation["sceneExposed"],
            first_presentation["requestedVisible"],
            first_presentation["sceneTitle"] == "莉莉丝 · 盒中世界",
            first_presentation["sceneOnTop"],
            first_presentation["stageFound"],
            first_presentation["manageActionFound"],
            first_presentation["exitActionFound"],
            first_presentation["presentationCount"] >= 1,
            first_presentation["sceneFitsLogicalScreen"],
        )
    )
    repeat_ok = all(
        (
            second_presentation["actionFound"],
            second_presentation["actionVisible"],
            second_presentation["actionEnabled"],
            second_presentation["nativeHit"],
            second_presentation["minimizedBefore"],
            second_presentation["restoredFromMinimized"],
            second_presentation["backendOpen"],
            second_presentation["sceneVisible"],
            second_presentation["sceneExposed"],
            second_presentation["sceneTitle"] == "莉莉丝 · 盒中世界",
            second_presentation["presentationCountAdvanced"],
            second_presentation["restoredIntoPetWorkArea"],
        )
    )
    return bool(first_ok and repeat_ok and all(closed.values()))


def main() -> int:
    previous_data_dir = os.environ.get("LILIES_DATA_DIR")
    backend: Backend | None = None
    app: QApplication | None = None
    try:
        # Keep the SQLite/socket directory alive until the backend and Qt
        # objects have released every handle, then let the context remove it.
        with tempfile.TemporaryDirectory(prefix="lilies-world-present-") as data_dir:
            os.environ["LILIES_DATA_DIR"] = data_dir
            try:
                QQuickWindow.setDefaultAlphaBuffer(True)
                app = QApplication([])
                backend = Backend(smoke=True, force_compact=True)
                backend._v03_timer.stop()
                passed = _run_verification(app, backend)
            finally:
                try:
                    if backend is not None:
                        backend.shutdown()
                finally:
                    if app is not None:
                        app.processEvents()
            return 0 if passed else 1
    finally:
        if previous_data_dir is None:
            os.environ.pop("LILIES_DATA_DIR", None)
        else:
            os.environ["LILIES_DATA_DIR"] = previous_data_dir


if __name__ == "__main__":
    raise SystemExit(main())
