from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_QPA_OFFSCREEN_SIZE", "1200x900")

from PySide6.QtCore import Property, QMetaObject, QObject, QPoint, QPointF, Qt, QUrl, Signal
from PySide6.QtGui import QFontDatabase, QWindow
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class SceneBackend(QObject):
    productivityChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._world = {
            "entered": True,
            "name": "莉莉丝的盒中空间",
            "totalCount": 6,
            "unlockedCount": 4,
            "placedCount": 3,
            "availableCount": 1,
            "growth": {
                "points": 346,
                "stage": "信赖",
                "nextStage": "亲近",
                "remaining": 354,
                "progress": 0.115,
            },
            "wardrobe": {
                "outfitId": "home-cardigan",
                "outfitName": "家居开衫",
                "poseId": "idle-prayer",
                "poseName": "抱拳祈祷",
            },
            "objects": [
                {
                    "object_id": "box-core",
                    "object_kind": "room",
                    "display_name": "莉莉丝的盒子",
                    "unlocked": True,
                    "placed": True,
                    "position": {"x": 0.52, "y": 0.55},
                    "unlockHint": "初始空间",
                },
                {
                    "object_id": "paper-shelf",
                    "object_kind": "furniture",
                    "display_name": "纸页架",
                    "unlocked": True,
                    "placed": True,
                    "position": {"x": 0.79, "y": 0.36},
                    "unlockHint": "完成一次完整的论文阅读",
                },
                {
                    "object_id": "rest-cushion",
                    "object_kind": "furniture",
                    "display_name": "休息软垫",
                    "unlocked": True,
                    "placed": True,
                    "position": {"x": 0.29, "y": 0.80},
                    "unlockHint": "完成三次主动休息",
                },
                {
                    "object_id": "workbench",
                    "object_kind": "furniture",
                    "display_name": "工作台",
                    "unlocked": True,
                    "placed": False,
                    "position": {},
                    "unlockHint": "完成三段专注",
                },
                {
                    "object_id": "living-corner",
                    "object_kind": "room",
                    "display_name": "生活角",
                    "unlocked": False,
                    "placed": False,
                    "position": {},
                    "unlockHint": "完成三件日常事项",
                },
                {
                    "object_id": "letter-rack",
                    "object_kind": "furniture",
                    "display_name": "信笺架",
                    "unlocked": False,
                    "placed": False,
                    "position": {},
                    "unlockHint": "完成一次 Slack 整理",
                },
            ],
        }

    @Property("QVariantMap", notify=productivityChanged)
    def boxWorldStatus(self) -> dict[str, object]:
        return self._world

    @Property("QVariantMap", notify=productivityChanged)
    def growthStatus(self) -> dict[str, object]:
        return dict(self._world["growth"])


def _load_font() -> None:
    candidate = PROJECT_ROOT / "assets" / "fonts" / "NotoSansSC-VF.ttf"
    if candidate.exists():
        QFontDatabase.addApplicationFont(str(candidate))


def _center(item: QQuickItem) -> QPoint:
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(point.x()), round(point.y()))


def _item(window: QQuickWindow, object_name: str) -> QQuickItem | None:
    pending = list(window.contentItem().childItems())
    while pending:
        candidate = pending.pop()
        if candidate.objectName() == object_name:
            return candidate
        pending.extend(candidate.childItems())
    return None


def _sample_image(window: QQuickWindow, artifact_name: str = "box-world-scene.png") -> dict[str, object]:
    image = window.grabWindow()
    artifact = PROJECT_ROOT / "artifacts" / artifact_name
    artifact.parent.mkdir(parents=True, exist_ok=True)
    saved = bool(image.save(str(artifact)))
    colors: set[int] = set()
    dark_samples = 0
    light_samples = 0
    if not image.isNull():
        step_x = max(1, image.width() // 48)
        step_y = max(1, image.height() // 36)
        for y in range(0, image.height(), step_y):
            for x in range(0, image.width(), step_x):
                color = image.pixelColor(x, y)
                colors.add(color.rgba())
                lightness = color.lightness()
                dark_samples += int(lightness < 90)
                light_samples += int(lightness > 205)
    return {
        "saved": saved,
        "path": str(artifact),
        "size": [image.width(), image.height()],
        "sampledColorCount": len(colors),
        "darkSamples": dark_samples,
        "lightSamples": light_samples,
    }


def _run(app: QApplication) -> dict[str, object]:
    backend = SceneBackend()
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(PROJECT_ROOT / "qml" / "V03BoxWorldScene.qml")
    ))
    if component.status() == QQmlComponent.Status.Error:
        raise RuntimeError("\n".join(error.toString() for error in component.errors()))

    scene = component.createWithInitialProperties(
        {
            "appBackend": backend,
            "presentationArea": {
                "left": 100,
                "top": 80,
                "right": 1100,
                "bottom": 780,
                "width": 1000,
                "height": 700,
            },
            "stayOnTopWhenPresented": True,
            "requestedVisible": True,
        }
    )
    if not isinstance(scene, QQuickWindow):
        errors = "\n".join(error.toString() for error in component.errors())
        raise RuntimeError(f"box-world scene failed to create\n{errors}")

    manage_requests: list[bool] = []
    exit_requests: list[bool] = []
    scene.manageDecorationsRequested.connect(lambda: manage_requests.append(True))
    scene.exitRequested.connect(lambda: exit_requests.append(True))

    try:
        QTest.qWait(650)
        stage = _item(scene, "boxWorldSceneStage")
        character = _item(scene, "boxWorldLilithImage")
        progress = _item(scene, "boxWorldResonanceProgress")
        greeting = _item(scene, "boxWorldGreetingText")
        manage = _item(scene, "boxWorldManageDecorationsButton")
        exit_button = _item(scene, "boxWorldSceneExitButton")
        full_screen = _item(scene, "boxWorldSceneFullScreenButton")
        placed_names = [
            name
            for name in (
                "boxWorldPlaced_box-core",
                "boxWorldPlaced_paper-shelf",
                "boxWorldPlaced_rest-cushion",
            )
            if _item(scene, name) is not None
        ]
        state_names = [
            name
            for name in (
                "boxWorldObjectState_box-core",
                "boxWorldObjectState_paper-shelf",
                "boxWorldObjectState_rest-cushion",
                "boxWorldObjectState_workbench",
                "boxWorldObjectState_living-corner",
                "boxWorldObjectState_letter-rack",
            )
            if _item(scene, name) is not None
        ]

        if manage is not None:
            QTest.mouseClick(scene, Qt.MouseButton.LeftButton, pos=_center(manage))
            QTest.qWait(80)

        first_geometry = scene.geometry()
        first = {
            "visible": bool(scene.isVisible()),
            "exposed": bool(scene.isExposed()),
            "title": scene.title(),
            "onTop": bool(scene.flags() & Qt.WindowType.WindowStaysOnTopHint),
            "size": [first_geometry.width(), first_geometry.height()],
            "insidePresentationArea": (
                first_geometry.left() >= 100
                and first_geometry.top() >= 80
                and first_geometry.right() <= 1100
                and first_geometry.bottom() <= 780
            ),
            "stageFound": stage is not None,
            "characterFound": character is not None,
            "progressFound": progress is not None,
            "manageFound": manage is not None,
            "exitFound": exit_button is not None,
            "fullScreenFound": full_screen is not None,
            "manageSignalCount": len(manage_requests),
            "placedDecorations": placed_names,
            "stateRows": state_names,
            "placedCount": int(scene.property("placedCount")),
            "unlockedCount": int(scene.property("unlockedCount")),
            "resonancePoints": int(scene.property("resonancePoints")),
            "greetingFits": bool(
                greeting is not None
                and float(greeting.property("paintedWidth")) <= greeting.width() + 1.5
                and float(greeting.property("paintedHeight")) <= greeting.height() + 1.5
            ),
        }
        screenshot = _sample_image(scene)

        # Re-entering the retained window must undo a minimized state and move
        # an old monitor coordinate back into the supplied presentation area.
        scene.setX(1450)
        scene.setY(1050)
        scene.showMinimized()
        QTest.qWait(120)
        minimized_before = scene.visibility() == QWindow.Visibility.Minimized
        invoked = QMetaObject.invokeMethod(
            scene, "present", Qt.ConnectionType.DirectConnection
        )
        QTest.qWait(220)
        restored_geometry = scene.geometry()
        restored_visible_width = max(
            0, min(restored_geometry.right(), 1100) - max(restored_geometry.left(), 100)
        )
        restored_visible_height = max(
            0, min(restored_geometry.bottom(), 780) - max(restored_geometry.top(), 80)
        )
        restored = {
            "invokeSucceeded": bool(invoked),
            "minimizedBefore": minimized_before,
            "notMinimized": scene.visibility() != QWindow.Visibility.Minimized,
            "visible": bool(scene.isVisible()),
            "reachable": (
                restored_visible_width >= min(160, restored_geometry.width() * 0.30)
                and restored_visible_height >= min(100, restored_geometry.height() * 0.24)
            ),
            "presentationCount": int(scene.property("presentationCount")),
        }

        # The same component is also the high-DPI/small-logical-area path.  It
        # keeps prose wrapped and moves the inventory into a roomy, scrollable
        # paper overlay instead of squeezing labels into a desktop-only rail.
        scene.setWidth(620)
        scene.setHeight(470)
        QTest.qWait(260)
        narrow_stage = _item(scene, "boxWorldSceneStage")
        narrow_panel = _item(scene, "boxWorldInfoPanel")
        narrow_title = _item(scene, "boxWorldSceneTitleBar")
        narrow_footer = _item(scene, "boxWorldSceneFooter")
        narrow_greeting = _item(scene, "boxWorldGreetingText")
        narrow_manage = _item(scene, "boxWorldManageDecorationsButton")
        narrow = {
            "enabled": bool(scene.property("narrowViewport")),
            "size": [scene.width(), scene.height()],
            "stageFound": narrow_stage is not None,
            "panelFound": narrow_panel is not None,
            "titleBarVisible": bool(narrow_title is not None and narrow_title.isVisible()),
            "footerVisible": bool(narrow_footer is not None and narrow_footer.isVisible()),
            "titleBarGeometry": (
                [round(narrow_title.x()), round(narrow_title.y()),
                 round(narrow_title.width()), round(narrow_title.height())]
                if narrow_title is not None else []
            ),
            "stageGeometry": (
                [round(narrow_stage.x()), round(narrow_stage.y()),
                 round(narrow_stage.width()), round(narrow_stage.height())]
                if narrow_stage is not None else []
            ),
            "footerGeometry": (
                [round(narrow_footer.x()), round(narrow_footer.y()),
                 round(narrow_footer.width()), round(narrow_footer.height())]
                if narrow_footer is not None else []
            ),
            "contentSize": [
                round(scene.contentItem().width()),
                round(scene.contentItem().height()),
            ],
            "panelInsideStage": bool(
                narrow_stage is not None
                and narrow_panel is not None
                and narrow_panel.x() >= narrow_stage.x()
                and narrow_panel.y() >= narrow_stage.y()
                and narrow_panel.x() + narrow_panel.width()
                <= narrow_stage.x() + narrow_stage.width() + 1
                and narrow_panel.y() + narrow_panel.height()
                <= narrow_stage.y() + narrow_stage.height() + 1
            ),
            "greetingFits": bool(
                narrow_greeting is not None
                and float(narrow_greeting.property("paintedWidth"))
                <= narrow_greeting.width() + 1.5
                and float(narrow_greeting.property("paintedHeight"))
                <= narrow_greeting.height() + 1.5
            ),
            "manageComfortable": bool(
                narrow_manage is not None and narrow_manage.height() >= 40
            ),
        }
        narrow_screenshot = _sample_image(scene, "box-world-scene-narrow.png")

        # A tiny logical work area (for example a 4K monitor at very high
        # scaling) must retain both exit controls and a readable amount of the
        # character.  The compact progress strip intentionally drops its
        # redundant summary line instead of covering nearly the whole scene.
        scene.setWidth(360)
        scene.setHeight(360)
        QTest.qWait(260)
        micro_stage = _item(scene, "boxWorldSceneStage")
        micro_panel = _item(scene, "boxWorldInfoPanel")
        micro_title_bar = _item(scene, "boxWorldSceneTitleBar")
        micro_title_text = _item(scene, "boxWorldSceneTitleText")
        micro_full_screen = _item(scene, "boxWorldSceneFullScreenButton")
        micro_exit = _item(scene, "boxWorldSceneExitButton")
        micro_manage = _item(scene, "boxWorldManageDecorationsButton")
        micro_character = _item(scene, "boxWorldLilithLayer")
        character_visible_ratio = 0.0
        if micro_panel is not None and micro_character is not None:
            panel_top = micro_panel.mapToScene(QPointF(0, 0)).y()
            character_top = micro_character.mapToScene(QPointF(0, 0)).y()
            character_visible_ratio = max(
                0.0,
                min(1.0, (panel_top - character_top) / max(1.0, micro_character.height())),
            )
        micro = {
            "enabled": bool(scene.property("microViewport")),
            "size": [scene.width(), scene.height()],
            "stageFound": micro_stage is not None,
            "panelFound": micro_panel is not None,
            "panelHeight": round(micro_panel.height()) if micro_panel is not None else 0,
            "panelInsideStage": bool(
                micro_stage is not None
                and micro_panel is not None
                and micro_panel.x() >= micro_stage.x()
                and micro_panel.y() >= micro_stage.y()
                and micro_panel.x() + micro_panel.width()
                <= micro_stage.x() + micro_stage.width() + 1
                and micro_panel.y() + micro_panel.height()
                <= micro_stage.y() + micro_stage.height() + 1
            ),
            "titleFits": bool(
                micro_title_text is not None
                and float(micro_title_text.property("paintedWidth"))
                <= micro_title_text.width() + 1.5
            ),
            "headerControlsInside": bool(
                micro_title_bar is not None
                and micro_full_screen is not None
                and micro_exit is not None
                and micro_full_screen.x() >= 0
                and micro_exit.x() >= 0
                and micro_exit.x() + micro_exit.width()
                <= micro_title_bar.width() + 1
            ),
            "manageComfortable": bool(
                micro_manage is not None and micro_manage.height() >= 32
            ),
            "characterVisibleRatio": round(character_visible_ratio, 3),
        }
        micro_screenshot = _sample_image(scene, "box-world-scene-micro.png")

        if exit_button is not None:
            QTest.mouseClick(scene, Qt.MouseButton.LeftButton, pos=_center(exit_button))
            QTest.qWait(100)
        exited = {
            "signalCount": len(exit_requests),
            "requestedVisible": bool(scene.property("requestedVisible")),
            "hidden": not bool(scene.isVisible()),
        }

        return {
            "firstPresentation": first,
            "screenshot": screenshot,
            "repeatAfterMinimize": restored,
            "narrowLayout": narrow,
            "narrowScreenshot": narrow_screenshot,
            "microLayout": micro,
            "microScreenshot": micro_screenshot,
            "exit": exited,
        }
    finally:
        scene.setProperty("requestedVisible", False)
        scene.hide()
        scene.deleteLater()
        app.processEvents()
        del engine


def main() -> int:
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    _load_font()
    outcome = _run(app)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    first = outcome["firstPresentation"]
    restored = outcome["repeatAfterMinimize"]
    narrow = outcome["narrowLayout"]
    micro = outcome["microLayout"]
    exited = outcome["exit"]
    screenshot = outcome["screenshot"]
    passed = all(
        (
            first["visible"],
            first["exposed"],
            first["title"] == "莉莉丝 · 盒中世界",
            first["onTop"],
            first["insidePresentationArea"],
            first["stageFound"],
            first["characterFound"],
            first["progressFound"],
            first["manageFound"],
            first["exitFound"],
            first["fullScreenFound"],
            first["manageSignalCount"] == 1,
            first["placedCount"] == 3,
            first["unlockedCount"] == 4,
            first["resonancePoints"] == 346,
            len(first["placedDecorations"]) == 3,
            len(first["stateRows"]) == 6,
            first["greetingFits"],
            screenshot["saved"],
            screenshot["sampledColorCount"] >= 40,
            screenshot["darkSamples"] > 0,
            screenshot["lightSamples"] > 0,
            restored["invokeSucceeded"],
            restored["minimizedBefore"],
            restored["notMinimized"],
            restored["visible"],
            restored["reachable"],
            restored["presentationCount"] >= 2,
            narrow["enabled"],
            narrow["stageFound"],
            narrow["panelFound"],
            narrow["titleBarVisible"],
            narrow["footerVisible"],
            narrow["panelInsideStage"],
            narrow["greetingFits"],
            narrow["manageComfortable"],
            outcome["narrowScreenshot"]["sampledColorCount"] >= 35,
            micro["enabled"],
            micro["stageFound"],
            micro["panelFound"],
            micro["panelHeight"] <= 98,
            micro["panelInsideStage"],
            micro["titleFits"],
            micro["headerControlsInside"],
            micro["manageComfortable"],
            micro["characterVisibleRatio"] >= 0.45,
            outcome["microScreenshot"]["sampledColorCount"] >= 30,
            exited["signalCount"] == 1,
            not exited["requestedVisible"],
            exited["hidden"],
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
