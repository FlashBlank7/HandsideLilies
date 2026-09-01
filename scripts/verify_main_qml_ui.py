from __future__ import annotations

"""Headless geometry/style contract for the box settings surfaces."""

import json
import os
import sys
import tempfile
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for value in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from PySide6.QtCore import QPointF, QUrl
from PySide6.QtGui import QColor
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.paths import qml_path
from verify_compact_ui import OffscreenBackend


def _required(root: object, item_type: type, object_name: str):
    item = root.findChild(item_type, object_name)
    if item is None:
        raise RuntimeError(f"missing QML item: {object_name}")
    return item


def _rect_in(item: QQuickItem, container: QQuickItem) -> list[float]:
    origin = item.mapToItem(container, QPointF(0.0, 0.0))
    return [
        round(float(origin.x()), 2),
        round(float(origin.y()), 2),
        round(float(origin.x()) + float(item.width()), 2),
        round(float(origin.y()) + float(item.height()), 2),
    ]


def _paper_color(item: QQuickItem) -> dict[str, object]:
    background = item.property("background")
    if background is None:
        return {"color": "", "opaqueBlack": True}
    color = background.property("color")
    if isinstance(color, QColor):
        return {
            "color": color.name(QColor.NameFormat.HexArgb),
            "opaqueBlack": color.alpha() > 0
            and color.red() == 0
            and color.green() == 0
            and color.blue() == 0,
        }
    return {"color": str(color), "opaqueBlack": str(color).lower() == "black"}


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="lilies-main-qml-ui-")
    os.environ["LILIES_DATA_DIR"] = temporary.name
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    backend = OffscreenBackend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    backend._productivity_timer.stop()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml failed to load")
    root = engine.rootObjects()[0]

    try:
        chat_window = _required(root, QQuickWindow, "chatWindow")
        companion_scroll = _required(root, QQuickItem, "companionSettingsPage")
        main_scroll = _required(root, QQuickItem, "mainSettingsPage")
        frequency = _required(root, QQuickItem, "companionFrequencyDraft")
        companion_targets = [
            _required(root, QQuickItem, name)
            for name in (
                "customCompanionMinutesDraft",
                "customCompanionDailyDraft",
                "applyCustomCompanionFrequency",
                "restoreSavedCompanionFrequency",
                "companionReopenUnreadButton",
                "companionMarkUnreadReadButton",
            )
        ]
        nav_targets = [
            _required(root, QQuickItem, name)
            for name in (
                "chatPageConversationButton",
                "chatPageMemoryButton",
                "chatPageReadingButton",
                "chatPageCompanionButton",
                "chatPageSettingsButton",
            )
        ]
        desktop_toggle = _required(root, QQuickItem, "compactDesktopShellToggle")
        wrapped_model_label = _required(root, QQuickItem, "modelFallbackDescription")

        backend.setChatOpen(True)
        chat_window.setProperty("page", 3)
        chat_window.show()
        app.processEvents()
        frequency.setProperty("currentIndex", 4)
        frequency.activated.emit(4)
        QTest.qWait(40)
        app.processEvents()

        companion_available = float(companion_scroll.property("availableWidth") or 0.0)
        companion_content = float(companion_scroll.property("contentWidth") or 0.0)
        companion_rects = {
            str(item.objectName()): _rect_in(item, companion_scroll)
            for item in companion_targets
        }
        companion_inside = all(
            rect[0] >= -1.0 and rect[2] <= companion_available + 1.0
            for rect in companion_rects.values()
        )

        chat_window.setProperty("page", 4)
        QTest.qWait(40)
        app.processEvents()
        main_available = float(main_scroll.property("availableWidth") or 0.0)
        main_content = float(main_scroll.property("contentWidth") or 0.0)
        main_rects = {
            "compactDesktopShellToggle": _rect_in(desktop_toggle, main_scroll),
            "modelFallbackDescription": _rect_in(wrapped_model_label, main_scroll),
        }
        main_inside = all(
            rect[0] >= -1.0 and rect[2] <= main_available + 1.0
            for rect in main_rects.values()
        )

        paper_colors = {
            str(item.objectName()): _paper_color(item)
            for item in (*nav_targets, desktop_toggle, *companion_targets[-4:])
        }
        paper_is_non_black = all(
            value["color"] and not value["opaqueBlack"]
            for value in paper_colors.values()
        )
        report = {
            "companion": {
                "availableWidth": round(companion_available, 2),
                "contentWidth": round(companion_content, 2),
                "rects": companion_rects,
                "inside": companion_inside,
            },
            "settings": {
                "availableWidth": round(main_available, 2),
                "contentWidth": round(main_content, 2),
                "rects": main_rects,
                "inside": main_inside,
            },
            "paperColors": paper_colors,
            "passed": bool(
                companion_available > 0
                and companion_content <= companion_available + 0.1
                and companion_inside
                and main_available > 0
                and main_content <= main_available + 0.1
                and main_inside
                and paper_is_non_black
            ),
        }
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1
    finally:
        backend.shutdown()
        engine.deleteLater()
        app.processEvents()
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
