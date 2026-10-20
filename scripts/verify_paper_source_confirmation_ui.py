"""Real QML clicks over offline paper fixtures; never touches the user's desktop."""
from __future__ import annotations

import json
import os
import tempfile

from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class OfflineModel:
    ready = True
    def explain(self, text):
        return "这是本次选区的解释，不包含论文或其他上下文。"
    def stop(self):
        pass


def main():
    output = ROOT / "artifacts" / "paper-source-confirmation"
    output.mkdir(parents=True, exist_ok=True)
    checks, warnings = {}, []
    passed = False
    with tempfile.TemporaryDirectory(prefix="data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        app.setFont(QFont("Microsoft YaHei UI", 10))
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        backend.selection._subscription = OfflineModel()
        paper = backend.papers.add("<b>不是富文本</b> · " + "很长的论文标题与消融实验 " * 12, "https://example.org/paper.pdf")
        backend.papers.update_position(paper["paper_id"], 14, "图五 · 用户标记")
        backend.papers.start(paper["paper_id"])
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            bubble = root.findChild(QQuickWindow, "selectionBubble")
            assert bubble

            def find(name):
                return next(item for item in descendants(bubble.contentItem()) if item.objectName() == name)

            def click(name):
                item = find(name)
                assert item.isVisible() and item.isEnabled(), name
                QTest.mouseMove(bubble, QPointF(bubble.width() / 2, 40).toPoint())
                QTest.qWait(200)
                point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
                assert 0 <= point.x() < bubble.width() and 0 <= point.y() < bubble.height(), (name, point)
                QTest.mouseClick(bubble, Qt.LeftButton, pos=point.toPoint())
                QTest.qWait(200)

            def explain(text):
                backend.selection._source_app = "notepad.exe"
                backend.selection._explain(text, 30, 40)
                for _ in range(200):
                    QTest.qWait(10)
                    if not backend.selection.bubble.get("busy"):
                        break
                assert not backend.selection.bubble.get("busy")
                QTest.qWait(180)

            explain("选自另一个窗口")
            click("selectionSaveAction")
            assert bubble.property("choosingSave") and not backend.database.reading_cards()
            assert bool(bubble.flags() & Qt.WindowDoesNotAcceptFocus)
            title = find("selectionSavePaperTitle")
            expression = QQmlExpression(engine.rootContext(), title, "Number(textFormat) === 0")
            assert expression.evaluate()[0] is True and not expression.hasError()
            assert "<b>不是富文本</b>" in title.property("text")
            assert "第 14 页" in find("selectionSavePaperLocation").property("text")
            assert find("selectionSavePaperSource").property("text") == paper["source_uri"]
            for width in (340, 430):
                bubble.setWidth(width)
                QTest.qWait(200)
                for name in ("selectionSaveToPaper", "selectionSaveIndependent", "selectionSaveCancel"):
                    item = find(name)
                    point = item.mapToScene(QPointF(item.width(), item.height()))
                    assert point.x() <= width and point.y() <= bubble.height(), (width, name, point)
                disclosure = find("selectionSaveDisclosure")
                bottom = disclosure.mapToScene(QPointF(0, disclosure.height()))
                assert bottom.y() < find("selectionSaveToPaper").mapToScene(QPointF(0, 0)).y()
                assert "未自动识别文档" in disclosure.property("text")
                assert bubble.grabWindow().save(str(output / f"confirmation-{width}.png"))
            click("selectionSaveCancel")
            assert not bubble.property("choosingSave") and not backend.database.reading_cards()
            checks["read_only_preview_plain_text_scrolls_and_cancel_no_write"] = True

            click("selectionSaveAction")
            click("selectionSaveIndependent")
            assert len(backend.database.reading_cards()) == 1
            assert not backend.papers.cards(paper["paper_id"])
            checks["independent_choice_never_invents_source"] = True

            explain("确认来自本篇的选区")
            click("selectionSaveAction")
            click("selectionSaveToPaper")
            assert len(backend.papers.cards(paper["paper_id"])) == 1
            assert backend.papers.cards(paper["paper_id"])[0]["page"] == 14
            checks["explicit_confirm_links_exact_page"] = True

            explain("稍后被替代的选区")
            click("selectionSaveAction")
            old = backend.selection._save_offer["id"]
            explain("新的选区")
            assert not bubble.property("choosingSave")
            assert not backend.confirmSelectionSave(old, "paper")["ok"]
            assert len(backend.database.reading_cards()) == 2
            checks["new_selection_closes_old_confirmation"] = True

            click("selectionSaveAction")
            backend.selection.set_privacy_suppressed(True)
            QTest.qWait(80)
            assert not bubble.isVisible() and not bubble.property("choosingSave")
            assert backend.selection._save_offer is None
            backend.selection.set_privacy_suppressed(False)
            explain("来源已经变化")
            click("selectionSaveAction")
            with backend.database.connect() as db:
                db.execute("UPDATE papers SET source_uri=? WHERE paper_id=?", ("https://example.org/changed.pdf", paper["paper_id"]))
            click("selectionSaveToPaper")
            assert find("selectionSaveError").isVisible()
            assert len(backend.database.reading_cards()) == 2
            assert bubble.grabWindow().save(str(output / "source-changed.png"))
            checks["privacy_cancel_and_changed_source_fail_without_partial_write"] = True

            assert not warnings, warnings
            passed = True
        finally:
            for window in app.topLevelWindows():
                window.hide()
            engine.deleteLater()
            QTest.qWait(30)
            backend.shutdown()
            app.processEvents()
            (output / "result.json").write_text(json.dumps({"passed": passed, "checks": checks, "warnings": warnings,
                "scope": "Offscreen QML clicks; synthetic files, offline text model, no native input or real reader."}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
