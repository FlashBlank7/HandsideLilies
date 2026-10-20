"""Evidence labels and explicit pasted-text workflow, with no native observation."""
from __future__ import annotations

import json
import os
import tempfile

from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class Model:
    ready = True
    def __init__(self): self.calls = []
    def explain(self, text):
        self.calls.append(text)
        return "两组均值相近，不代表波动相近。还要看方差，以及样本量是否足够。"
    def stop(self): pass


def main():
    output = ROOT / "artifacts" / "awareness"
    output.mkdir(parents=True, exist_ok=True)
    checks, warnings = {}, []
    passed = False
    with tempfile.TemporaryDirectory(prefix="test-data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        app.setFont(QFont("Microsoft YaHei UI", 10))
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        backend.selection.set_enabled(False)
        model = Model()
        backend.selection._subscription = model
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))

        def find(window, name):
            item = next((item for item in descendants(window.contentItem()) if item.objectName() == name), None)
            assert item is not None, name
            return item

        def click(window, name):
            item = find(window, name)
            assert item.isVisible() and item.isEnabled(), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (name, point)
            QTest.mouseClick(window, Qt.LeftButton, pos=point.toPoint())
            QTest.qWait(120)

        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            chat = root.findChild(QQuickWindow, "chatWindow")
            companion = root.findChild(QQuickWindow, "companionBubbleWindow")
            selected = root.findChild(QQuickWindow, "selectionBubble")
            assert chat and companion and selected
            backend.setChatOpen(True)
            chat.setProperty("page", 3)
            chat.resize(520, 840)
            chat.show()
            QTest.qWait(140)
            scroll = find(chat, "companionSettingsPage")
            target = find(chat, "companionProvidedTextButton")
            point = target.mapToScene(QPointF(0, 0))
            if point.y() > chat.height() - 60:
                scroll.property("contentItem").setProperty("contentY", point.y() - chat.height() + 140)
                QTest.qWait(100)
            assert find(chat, "companionLatestBasisLabel").property("text") == "本次没有观察"
            assert find(chat, "companionCaptureBasisLabel").property("text") == "本次没有观察"
            assert not backend.companionService.activityStatus["smartObservationEnabled"]
            checks["permission_not_presented_as_actual_observation"] = True

            click(chat, "companionProvidedTextButton")
            editor = find(chat, "providedTextInput")
            editor.setProperty("text", "Both groups have similar means, but unequal variance. <b>not markup</b>")
            assert chat.grabWindow().save(str(output / "provided-text-520.png"))
            QTest.keyClick(chat, Qt.Key_Escape)
            QTest.qWait(100)
            click(chat, "companionProvidedTextButton")
            assert "unequal variance" in editor.property("text")
            click(chat, "providedTextSendButton")
            for _ in range(200):
                QTest.qWait(10)
                if not backend.selectionBubble.get("busy"): break
            assert model.calls == ["Both groups have similar means, but unequal variance. <b>not markup</b>"]
            assert backend.selectionBubble["awareness"]["kind"] == "selected-text"
            assert not backend.database.get_setting("selection_monitor_enabled", True)
            assert not backend.companionService.activityStatus["smartObservationEnabled"]
            assert find(selected, "selectionContextLabel").property("text") == "你选的文字"
            assert not backend.database.reading_cards()
            QTest.qWait(180)
            assert selected.grabWindow().save(str(output / "selected-text.png"))
            checks["explicit_text_is_stateless_and_keeps_monitoring_off"] = True
            backend.selection.dismiss()

            def show(kind, label, **extra):
                backend.companionService._bubble = {"id": kind, "visible": True, "summary": "两组均值相近，波动却可能相差很远。",
                    "detail": "这里是排版样例，不代表访问了任何真实窗口。", "category": "科普", "sceneLabel": "文档工作",
                    "contextType": kind, **extra}
                backend.companionService.bubbleChanged.emit()
                QTest.qWait(180)
                item = find(companion, "companionSceneLabel")
                assert item.property("text") == label, item.property("text")
                assert bool(companion.flags() & Qt.WindowDoesNotAcceptFocus)
                assert companion.grabWindow().save(str(output / (kind + ".png")))
                return item

            item = show("application-signal", "只知道应用")
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            QTest.mouseMove(companion, point.toPoint())
            QTest.qWait(600)
            assert "没有窗口标题、正文或截图" in item.property("description")
            checks["application_bubble_has_visible_input_label_and_plain_tooltip"] = True
            show("active-window-image", "本条用过截图", imageGrounded=True)
            show("retained-image-anchor", "沿用上次线索")
            show("reply", "沿用这段对话", replyBasis="dialogue")
            show("old", "之前的气泡", restoredUnread=True)
            show("unrecorded", "依据未记录")
            checks["image_anchor_reply_and_history_have_distinct_receipts"] = True
            assert not warnings, warnings
            checks["no_qml_warnings_or_focus_steal"] = True
            passed = True
        finally:
            for window in app.topLevelWindows(): window.hide()
            engine.deleteLater()
            QTest.qWait(30)
            backend.shutdown()
            app.processEvents()
            (output / "result.json").write_text(json.dumps({"passed": passed, "checks": checks, "warnings": warnings,
                "environment": "offscreen; synthetic bubbles and local model stub; no native capture, clipboard read, network or real input"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__": main()
