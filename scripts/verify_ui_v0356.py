"""Real QML, synthetic data, offscreen input only; no desktop or model access."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPointF, Qt, QUrl, QMetaObject, Q_ARG, Q_RETURN_ARG
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts


def descendants(item):
    yield item
    for child in item.childItems():
        yield from descendants(child)


def main():
    output = ROOT / "artifacts" / "ui-v0356"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lilies-ui-v0356-") as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        app.setFont(QFont("Microsoft YaHei UI", 10))
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
        if not engine.rootObjects():
            backend.shutdown()
            raise AssertionError(warnings)
        root = engine.rootObjects()[0]
        chat = root.findChild(QQuickWindow, "chatWindow")

        def find(name):
            item = next((i for i in descendants(chat.contentItem()) if i.objectName() == name), None)
            assert item is not None, name
            return item

        def click(name):
            item = find(name)
            assert item.isVisible() and item.isEnabled(), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            assert 0 <= point.x() <= chat.width() and 0 <= point.y() <= chat.height(), (name, point)
            QTest.mouseClick(chat, Qt.LeftButton, pos=point.toPoint())
            QTest.qWait(60)

        def capture(name):
            QTest.qWait(100)
            assert chat.grabWindow().save(str(output / (name + ".png")))

        try:
            backend.setChatOpen(True)
            chat.resize(680, 840)
            chat.show()
            QTest.qWait(150)
            # Authored layout fixtures, not a claim about live model replies.
            backend._chat_timeline.reset_messages([
                {"role": "user", "content": "今天又存了十篇论文，一篇都没读。"},
                {"role": "assistant", "content": "收藏夹先替你读上博士了。\n挑一篇？先看它到底想回答什么。"},
                {"role": "user", "content": "就先看手边这篇吧。"},
                {"role": "assistant", "content": "嗯。先看看它问了什么，再决定要不要往下读。"},
            ])
            chat.setProperty("page", 0)
            capture("conversation")
            assert not find("chatHistoryInput").isVisible()
            click("chatHistoryToggle")
            assert find("chatHistoryInput").isVisible()
            find("chatHistoryInput").setProperty("text", "旧记录")
            click("chatHistoryToggle")
            assert not find("chatHistoryInput").isVisible()
            assert find("chatHistoryInput").property("text") == ""

            # Plain source text must stay inert even though paragraph spacing
            # is rendered with a small, escaped rich-text wrapper.
            literal = '<img src="file:///private.png"> & <b>原话</b>\n第二行'
            backend._chat_timeline.reset_messages([{"role": "assistant", "content": literal}])
            QTest.qWait(80)
            body = find("chatMessageBody")
            plain = QMetaObject.invokeMethod(body, "getText", Q_RETURN_ARG(str),
                                            Q_ARG(int, 0), Q_ARG(int, body.property("length")))
            # QTextCursor exposes a line-break as U+2028, not a paragraph LF.
            assert plain.replace("\u2028", "\n") == literal, plain

            checks = []
            for width, height in [(680, 840), (520, 700)]:
                chat.resize(width, height)
                for page, prefix, categories in [
                    (4, "settingsNavigation", ["daily", "pet", "desktop", "model", "data"]),
                    (3, "companionNavigation", ["daily", "frequency", "interests", "observation", "sources"]),
                ]:
                    chat.setProperty("page", page)
                    QTest.qWait(80)
                    for category in categories:
                        click(prefix + "_" + category)
                        navigation = find(prefix)
                        assert navigation.property("section") == category
                        scroll = find("mainSettingsPage" if page == 4 else "companionSettingsPage")
                        assert float(scroll.property("contentWidth")) <= float(scroll.property("availableWidth")) + 1
                        assert abs(scroll.property("contentItem").property("contentY")) <= 1
                        # All category buttons remain on-screen at the small width.
                        for item in descendants(navigation):
                            if item.objectName().startswith(prefix + "_"):
                                point = item.mapToScene(QPointF(item.width(), item.height()))
                                assert point.x() <= width + 1 and point.y() <= height + 1
                        checks.append(f"{width}:{prefix}:{category}")
                        capture(f"{prefix}-{category}-{width}")

            chat.resize(680, 840)
            chat.setProperty("page", 4)
            nav = find("settingsNavigation")
            nav.setProperty("query", "拖动")
            QTest.qWait(80)
            assert nav.property("matchingIds").toVariant() == ["movement"]
            capture("search")
            nav.setProperty("query", "not-a-setting")
            QTest.qWait(50)
            assert nav.property("matchingIds").toVariant() == []
            click("settingsNavigationClearSearch")
            click("settingsNavigation_pet")
            capture("settings")

            # Editing frequency, leaving the page and returning must preserve
            # the draft and must not write it until the user presses Apply.
            chat.setProperty("page", 3)
            click("companionNavigation_frequency")
            frequency = find("companionFrequencyDraft")
            frequency.setProperty("currentIndex", 4)
            frequency.activated.emit(4)
            QTest.qWait(60)
            minutes = find("customCompanionMinutesDraft")
            editor = minutes.property("contentItem")
            editor.forceActiveFocus()
            QTest.keyClick(chat, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClick(chat, Qt.Key_1)
            QTest.keyClick(chat, Qt.Key_7)
            click("companionNavigation_daily")
            click("companionNavigation_frequency")
            assert minutes.property("value") == 17
            assert backend.companionService.preferences.get("customMinimumMinutes", 25) != 17
            click("applyCustomCompanionFrequency")
            assert backend.companionService.preferences["customMinimumMinutes"] == 17
            capture("frequency")
            click("companionNavigation_daily")
            capture("companion")
            # Work/wardrobe share the same visual tokens; render the real panel
            # too, still on Qt's offscreen platform and a throwaway database.
            backend.setChatOpen(False)
            backend.openWorkPanelSection("work")
            work = root.findChild(QQuickWindow, "v03WorkPanel")
            work.resize(920, 800)
            QTest.qWait(180)
            assert work.grabWindow().save(str(output / "work.png"))
            backend.setWorkPanelOpen(False)
            assert not warnings, warnings
            report = {"passed": True, "checks": checks, "qmlWarnings": warnings,
                      "draftSurvivesNavigation": True, "searchWorks": True,
                      "inertRichText": True, "fixtureDialogue": True,
                      "output": str(output)}
            (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
            return 0
        finally:
            for window in app.topLevelWindows():
                window.hide()
            backend.shutdown()
            engine.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
