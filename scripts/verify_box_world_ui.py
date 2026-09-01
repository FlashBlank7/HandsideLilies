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

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QApplication

from lilies.backend import Backend
from lilies.paths import qml_path


def load_ui_font() -> None:
    candidates = (
        PROJECT_ROOT / "assets" / "fonts" / "NotoSansSC-VF.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
    )
    for candidate in candidates:
        if candidate.is_file() and QFontDatabase.addApplicationFont(str(candidate)) >= 0:
            return


def visual_descendants(item: QQuickItem) -> list[QQuickItem]:
    result: list[QQuickItem] = []
    pending = list(item.childItems())
    while pending:
        child = pending.pop()
        result.append(child)
        pending.extend(child.childItems())
    return result


def run_verification() -> dict[str, object]:
    backend: Backend | None = None
    app: QApplication | None = None
    outcome: dict[str, object] = {}
    try:
        QQuickWindow.setDefaultAlphaBuffer(True)
        app = QApplication([])
        load_ui_font()
        backend = Backend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(qml_path())))
        if not engine.rootObjects():
            raise RuntimeError("Main.qml failed to load")

        root = engine.rootObjects()[0]
        scene = root.findChild(QQuickWindow, "boxWorldSceneWindow")
        if scene is None:
            raise RuntimeError("V03BoxWorldScene failed to load")

        def capture() -> None:
            assert backend is not None
            assert app is not None
            image = scene.grabWindow()
            screenshot = PROJECT_ROOT / "artifacts" / "box-world-scene-integrated.png"
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            saved = image.save(str(screenshot))
            names = {
                str(item.objectName())
                for item in visual_descendants(scene.contentItem())
                if item.objectName()
            }
            objects = list(backend.boxWorldStatus.get("objects", []))
            outcome.update(
                {
                    "sceneOpen": bool(backend.boxWorldSceneOpen),
                    "requestedVisible": bool(scene.property("requestedVisible")),
                    "visible": bool(scene.isVisible()),
                    "exposed": bool(scene.isExposed()),
                    "windowTitle": str(scene.title()),
                    "objects": objects,
                    "renderedObjectRows": len(
                        [name for name in names if name.startswith("boxWorldObjectState_")]
                    ),
                    "renderedPlacedDecorations": len(
                        [name for name in names if name.startswith("boxWorldPlaced_")]
                    ),
                    "stageFound": "boxWorldSceneStage" in names,
                    "characterFound": "boxWorldLilithImage" in names,
                    "progressFound": "boxWorldResonanceProgress" in names,
                    "manageActionFound": "boxWorldManageDecorationsButton" in names,
                    "exitActionFound": "boxWorldSceneExitButton" in names,
                    "sceneSize": [scene.width(), scene.height()],
                    "screenshot": str(screenshot),
                    "screenshotSaved": bool(saved),
                }
            )
            app.quit()

        backend.enterBoxWorld()
        QTimer.singleShot(650, capture)
        app.exec()
        return outcome
    finally:
        try:
            if backend is not None:
                backend.shutdown()
        finally:
            if app is not None:
                app.processEvents()


def main() -> int:
    previous_data_dir = os.environ.get("LILIES_DATA_DIR")
    try:
        # The Qt/backend lifecycle is fully closed inside run_verification()
        # before this context attempts to remove its SQLite directory.
        with tempfile.TemporaryDirectory(prefix="lilies-box-world-ui-") as data_dir:
            os.environ["LILIES_DATA_DIR"] = data_dir
            outcome = run_verification()
    finally:
        if previous_data_dir is None:
            os.environ.pop("LILIES_DATA_DIR", None)
        else:
            os.environ["LILIES_DATA_DIR"] = previous_data_dir

    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    if not outcome.get("screenshotSaved"):
        return 1
    if not all(
        bool(outcome.get(name))
        for name in (
            "sceneOpen",
            "requestedVisible",
            "visible",
            "exposed",
            "stageFound",
            "characterFound",
            "progressFound",
            "manageActionFound",
            "exitActionFound",
        )
    ):
        return 2
    if outcome.get("windowTitle") != "莉莉丝 · 盒中世界":
        return 3
    if outcome.get("renderedObjectRows") != 6:
        return 4
    if int(outcome.get("renderedPlacedDecorations", 0)) < 1:
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
