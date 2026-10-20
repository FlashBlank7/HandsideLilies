"""Exercise the paper notebook in Main.qml, with synthetic data and no live UI/network."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

# Sets offscreen BEFORE importing Qt or the backend.
from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class SelectionModel:
    ready = True
    calls = []

    def explain(self, source):
        self.calls.append(source)
        return "均值差不多，不代表波动也一样。这里还要比较方差，并确认两组的样本量。"

    def stop(self):
        pass


def main():
    output = ROOT / "artifacts" / "paper-notebook"
    output.mkdir(parents=True, exist_ok=True)
    checks, warnings, opened = {}, [], []
    complete = False
    with tempfile.TemporaryDirectory(prefix="test-data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        app.setFont(QFont("Microsoft YaHei UI", 10))
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        backend.selection._subscription = SelectionModel()
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))

        def find(name):
            item = next((item for item in descendants(chat.contentItem()) if item.objectName() == name), None)
            assert item is not None, name
            return item

        def click(name):
            item = find(name)
            assert item.isVisible() and item.isEnabled(), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            assert 0 <= point.x() < chat.width() and 0 <= point.y() < chat.height(), (name, point)
            QTest.mouseClick(chat, Qt.LeftButton, pos=point.toPoint())
            QTest.qWait(100)

        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            chat = root.findChild(QQuickWindow, "chatWindow")
            backend.openReadingDesk()
            chat.resize(680, 840)
            chat.show()
            QTest.qWait(140)
            click("paperAddLinkToggle")
            find("paperSourceInput").setProperty("text", "https://example.org/experiment.pdf")
            find("paperTitleInput").setProperty("text", "对照实验里的均值与方差")
            click("paperAddButton")
            paper_id = backend.paperState["current"]["paper_id"]
            assert not backend.paperState["active"]
            assert not find("paperDesk").property("adding")
            checks["explicit_add_does_not_start_observation_or_model"] = True

            click("paperPositionToggle")
            find("paperPageInput").setProperty("text", "7")
            find("paperLocatorInput").setProperty("text", "消融实验 · 表三")
            find("paperNextStepInput").setProperty("text", "先比较两个实验组的方差")
            backend.refreshReadingCards()
            QTest.qWait(80)
            assert find("paperPageInput").property("text") == "7"
            click("paperSavePositionButton")
            assert backend.paperState["current"]["last_page"] == 7
            checks["progress_draft_survives_background_refresh"] = True

            click("paperStartFinishButton")
            assert backend.paperState["active"]
            backend.selection._source_app = "wpspdf.exe"
            backend.selection._last_text = "The two groups have similar means but unequal variance."
            backend.selection._explain(backend.selection._last_text, 40, 80)
            for _ in range(200):
                QTest.qWait(10)
                if not backend.selection.bubble.get("busy"):
                    break
            assert not backend.selection.bubble.get("busy")
            bubble = root.findChild(QQuickWindow, "selectionBubble")
            def bubble_click(name):
                item = next(item for item in descendants(bubble.contentItem()) if item.objectName() == name)
                assert item.isVisible() and item.isEnabled(), name
                QTest.mouseMove(bubble, QPointF(bubble.width() / 2, 40).toPoint())
                QTest.qWait(200)
                point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
                assert 0 <= point.x() < bubble.width() and 0 <= point.y() < bubble.height()
                QTest.mouseClick(bubble, Qt.LeftButton, pos=point.toPoint())
                QTest.qWait(180)
            bubble_click("selectionSaveAction")
            assert bubble.property("choosingSave"), {
                "error": bubble.property("saveError"), "offer": backend.selection._save_offer,
                "bubble": backend.selection.bubble, "suppressed": backend.companionSuppressed,
            }
            assert not backend.database.reading_cards()
            assert bool(bubble.flags() & Qt.WindowDoesNotAcceptFocus)
            assert bubble.grabWindow().save(str(output / "paper-source-confirmation.png"))
            bubble_click("selectionSaveToPaper")
            backend.selection.dismiss()
            QTest.qWait(100)
            assert SelectionModel.calls == [backend.selection._last_text]
            assert backend.paperState["cards"][0]["page"] == 7
            assert backend.paperState["cards"][0]["locator"] == "消融实验 · 表三"
            checks["selection_saved_with_confirmed_provenance_without_model_context"] = True

            click("paperCardDetails_0")
            click("paperMark_understood")
            assert backend.paperState["review"]["markedCount"] == 1
            with patch.object(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True):
                click("paperCardOpenSource")
            assert opened == ["https://example.org/experiment.pdf#page=7"]
            assert chat.grabWindow().save(str(output / "card-details.png"))
            click("paperCardClose")
            checks["card_mark_and_original_source_click"] = True

            click("paperQuestionToggle")
            find("paperQuestionInput").setProperty("text", "均值接近的时候，作者为什么认为方法更稳定？")
            click("paperQuestionSave")
            assert backend.paperState["review"]["questionCount"] == 1
            backend.database.save_reading_card("还没整理的摘录", "不能自动当成重点", paper_context=backend.papers.capture_context())
            backend.refreshReadingCards()
            QTest.qWait(80)
            click("paperStartFinishButton")
            assert not backend.paperState["active"]
            assert find("paperDesk").property("reviewing")
            assert backend.paperState["cardPage"]["total"] == 2
            assert all(card["mark"] != "unmarked" for card in backend.paperState["cards"])
            checks["finish_reviews_only_marked_material"] = True
            assert chat.grabWindow().save(str(output / "paper-review-680.png"))
            chat.resize(520, 700)
            QTest.qWait(140)
            assert chat.grabWindow().save(str(output / "paper-review-520.png"))
            click("paperStartFinishButton")
            assert backend.paperState["active"] and backend.paperState["current"]["last_page"] == 7
            checks["continue_retains_page_and_next_step"] = True

            click("paperAllCardsTab")
            find("paperCardSearch").setProperty("text", "对照实验")
            find("paperCardSearch").forceActiveFocus()
            QTest.keyClick(chat, Qt.Key_Return)
            QTest.qWait(80)
            assert backend.paperState["cardPage"]["total"] == 3
            assert all(card["paper_id"] == paper_id for card in backend.paperState["cards"])
            assert chat.grabWindow().save(str(output / "all-cards-520.png"))
            checks["global_search_keeps_paper_provenance"] = True
            click("paperCardDetails_0")
            click("paperCardDelete")
            assert backend.paperState["cardPage"]["total"] == 3
            click("paperDeleteConfirm")
            assert backend.paperState["cardPage"]["total"] == 2
            checks["delete_requires_second_explicit_click"] = True
            with backend.database.connection_session():
                for index in range(43):
                    backend.database.save_reading_card("分页测试 " + str(index), "解释", paper_context=backend.papers.capture_context())
            backend.refreshReadingCards()
            QTest.qWait(100)
            assert backend.paperState["cardPage"]["total"] == 45
            click("paperCardsNext")
            assert backend.paperState["cardPage"]["offset"] == 40
            assert len(backend.paperState["cards"]) == 5
            click("paperCardsPrevious")
            assert backend.paperState["cardPage"]["offset"] == 0
            checks["card_pagination_reaches_older_items"] = True
            assert not warnings, warnings
            checks["no_qml_warnings"] = True
            complete = True
        finally:
            for window in app.topLevelWindows():
                window.hide()
            engine.deleteLater()
            QTest.qWait(30)
            backend.shutdown()
            app.processEvents()
            (output / "result.json").write_text(json.dumps({"checks": checks, "warnings": warnings, "passed": complete, "environment": "offscreen; synthetic paper; offline selection model; source opening intercepted"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
