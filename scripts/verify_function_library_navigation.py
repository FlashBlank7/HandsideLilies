"""Exercise visible library entrances with mouse clicks, not route calls.

All writes are isolated fixtures. No real user data, desktop capture or model.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_QUICK_BACKEND"] = "software"
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lilies.paths import configure_qt_cache_environment
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts


def descendants(item):
    yield item
    for child in item.childItems():
        yield from descendants(child)


def main():
    (ROOT / "artifacts").mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="library-click-", dir=ROOT / "artifacts")
    os.environ["LILIES_DATA_DIR"] = temporary.name
    configure_qt_cache_environment(Path(temporary.name))
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    load_windows_ui_fonts()
    backend = OffscreenBackend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    backend._productivity_timer.stop()
    # Mirror a full menu, with memory not pinned. Capacity must not disable use.
    for action in ("focus", "peek", "wardrobe"):
        backend.setQuickActionPinned(action, True)
    pinned_before = backend.database.get_setting("desktop_pet_quick_actions_v1")
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    qml_file = Path(os.environ.get("LILIES_TEST_QML", str(ROOT / "qml/Main.qml")))
    engine.load(QUrl.fromLocalFile(str(qml_file)))
    assert engine.rootObjects(), warnings
    root = engine.rootObjects()[0]
    chat = root.findChild(QQuickWindow, "chatWindow")
    work = root.findChild(QQuickWindow, "v03WorkPanel")
    navigation = root.findChild(QQuickItem, "settingsNavigation")
    scroll = root.findChild(QQuickItem, "mainSettingsPage")
    results = []

    def find(name):
        return next((i for i in descendants(chat.contentItem()) if i.objectName() == name), None)

    def click(target):
        assert target is not None and target.isVisible() and target.isEnabled(), "Entrance missing or disabled"
        flickable = scroll.property("contentItem")
        if chat.property("page") == 4:
            relative = target.mapToItem(flickable, QPointF(0, 0))
            maximum = max(0, float(flickable.property("contentHeight")) - flickable.height())
            flickable.setProperty("contentY", min(maximum, max(0, float(flickable.property("contentY")) + relative.y() - 40)))
            QTest.qWait(80)
        point = target.mapToScene(QPointF(target.width()/2, target.height()/2))
        assert 0 <= point.x() < chat.width() and 0 <= point.y() < chat.height(), "Click outside visible window"
        QTest.mouseClick(chat, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(round(point.x()), round(point.y())))
        QTest.qWait(180)

    try:
        for mode in ("Open", "LaunchArea"):
            for action in ("memory", "wardrobe"):
                backend.setWorkPanelOpen(False)
                backend.setChatOpen(True)
                chat.setProperty("page", 4)
                navigation.setProperty("section", "daily")
                QTest.qWait(200)
                if action == "memory":
                    assert not find("functionLibraryPin_memory").isEnabled(), "Fixture must fill all pin slots"
                target = find("functionLibrary" + mode + "_" + action)
                click(target)
                if action == "memory":
                    passed = backend.chatOpen and chat.isVisible() and chat.property("page") == 5 and not work.isVisible()
                else:
                    passed = backend.workPanelOpen and work.isVisible() and work.property("activeSection") == "growth" and not chat.isVisible()
                results.append(dict(entrance=mode, action=action, passed=bool(passed)))
                assert passed, results[-1]
                assert backend.database.get_setting("desktop_pet_quick_actions_v1") == pinned_before
        # Reopening an explicit chat page must retire the covering work panel.
        root.presentChatPage(5)
        QTest.qWait(180)
        assert chat.isVisible() and not work.isVisible()
        assert not warnings, warnings
        report = dict(passed=True, clicks=results, pinSelectionPreserved=True, qmlWarnings=warnings)
        print(json.dumps(report, ensure_ascii=True))
        return 0
    finally:
        for window in app.topLevelWindows():
            window.hide()
        backend.shutdown()
        engine.deleteLater()
        app.processEvents()
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
