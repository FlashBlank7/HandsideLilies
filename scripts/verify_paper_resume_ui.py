"""Paper continuity across a real Backend restart; owned offscreen UI only."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

# Configure Qt before loading it; never target the installed instance.
from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class NoModel:
    ready = False
    calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("This local continuity flow must not request a model")

    explain = complete

    def stop(self):
        pass

    abort = stop


def wait(predicate, label):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        QTest.qWait(20)
        if predicate():
            return
    raise AssertionError(label)


class Notebook:
    def __init__(self, warnings, models):
        self.backend = OffscreenBackend(smoke=True, force_compact=True)
        self.backend.offscreen_cursor = {"x": 300, "y": 300}
        self.backend._v03_timer.stop()
        self.backend._productivity_timer.stop()
        self.model = NoModel()
        models.append(self.model)
        self.backend.chat._gpt = self.model
        self.backend.selection._subscription = self.model
        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        self.engine.rootContext().setContextProperty("backend", self.backend)
        self.engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        self.engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
        assert self.engine.rootObjects(), warnings
        self.root = self.engine.rootObjects()[0]
        self.chat = self.root.findChild(QQuickWindow, "chatWindow")
        self.backend.openReadingDesk()
        self.chat.resize(680, 840)
        wait(self.chat.isVisible, "notebook presentation")

    def find(self, name, window=None):
        return next(i for i in descendants((window or self.chat).contentItem()) if i.objectName() == name)

    def click(self, name, window=None):
        window = window or self.chat
        # Worker completion precedes Qt Quick's next layout/render pass. Measure
        # the button after polish, not at the previous list/detail coordinates.
        QTest.qWait(100)
        item = self.find(name, window)
        assert item.isVisible() and item.isEnabled(), name
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (name, point)
        QTest.mouseClick(window, Qt.LeftButton, pos=point.toPoint())
        QTest.qWait(100)

    def search_paper(self, title):
        controller = self.backend.boxSearch
        self.backend.openBoxSearch()
        window = self.root.findChild(QQuickWindow, "boxSearchWindow")
        wait(window.isVisible, "search visible")
        window.setProperty("category", "paper")
        editor = self.find("boxSearchQuery", window)
        editor.setProperty("text", title)
        editor.forceActiveFocus()
        wait(lambda: not controller.state["busy"] and controller.state["query"] == title, "search complete")
        assert len(controller.state["items"]) == 1
        QTest.keyClick(window, Qt.Key_Return)
        wait(lambda: not controller.state["open"] or bool(controller.state["error"]), "search route")

    def filter_all_cards(self):
        if not self.find("paperDesk").property("allCards"):
            self.click("paperAllCardsTab")
        field = self.find("paperCardSearch")
        field.setProperty("text", "找不到的旧筛选")
        field.forceActiveFocus()
        QTest.keyClick(self.chat, Qt.Key_Return)
        QTest.qWait(100)
        assert self.backend.paperState["cardPage"]["total"] == 0

    def close(self):
        for window in QApplication.topLevelWindows():
            window.hide()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QTest.qWait(30)
        self.backend.shutdown()
        self.backend.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QTest.qWait(30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "artifacts").resolve()) or output.exists():
        raise ValueError("Use a new report under this checkout's artifacts directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    checks, observations, warnings, opened, models = {}, {}, [], [], []
    app = QApplication.instance() or QApplication([])
    load_windows_ui_fonts()
    app.setFont(QFont("Microsoft YaHei UI", 10))
    ui = None
    error = ""
    temporary = tempfile.TemporaryDirectory(prefix="paper-resume-", dir=output.parent)
    try:
        with patch.object(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True):
            os.environ["LILIES_DATA_DIR"] = temporary.name
            os.environ["QML_DISABLE_DISK_CACHE"] = "1"
            ui = Notebook(warnings, models)
            backend = ui.backend
            other = backend.papers.add("另外一篇论文", "https://example.org/other.pdf")
            backend._refresh_papers()
            ui.click("paperAddLinkToggle")
            ui.find("paperTitleInput").setProperty("text", "方差实验续读")
            ui.find("paperSourceInput").setProperty("text", "https://example.org/variance.pdf")
            ui.click("paperAddButton")
            paper_id = backend.paperState["current"]["paper_id"]
            ui.click("paperPositionToggle")
            ui.find("paperPageInput").setProperty("text", "7")
            ui.find("paperLocatorInput").setProperty("text", "消融实验 · 表三")
            ui.find("paperNextStepInput").setProperty("text", "核对两组的方差")
            ui.click("paperSavePositionButton")
            ui.click("paperStartFinishButton")
            ui.click("paperQuestionToggle")
            question = "均值接近时，为什么认为方法更稳定？"
            ui.find("paperQuestionInput").setProperty("text", question)
            ui.click("paperQuestionSave")
            ui.click("paperStartFinishButton")
            checks["explicit_read_question_finish"] = (
                not backend.paperState["active"] and backend.paperState["review"]["questionCount"] == 1
                and ui.find("paperDesk").property("reviewing"))

            # Preserve an explicit work bookmark without enumerating real windows.
            resumption = backend.workResumption
            resumption.capture()
            wait(lambda: not resumption.state["busy"], "capture empty owned layout")
            assert not resumption.state["draft"]["windows"]
            resumption.save({"captureId": resumption.state["draft"]["id"],
                "title": "继续方差实验", "nextStep": "核对两组的方差",
                "sources": ["https://example.org/variance.pdf"], "windowIds": []})
            wait(lambda: not resumption.state["busy"], "save bookmark")
            assert not resumption.state["error"], resumption.state
            bookmark_id = resumption.state["page"]["items"][0]["record_id"]
            ui.close()
            ui = None

            # Reconstruct both Backend and production Main.qml against the SAME fixture.
            ui = Notebook(warnings, models)
            backend = ui.backend
            current = backend.paperState["current"]
            checks["restart_retains_paper_page_next_step_and_question"] = (
                current["paper_id"] == paper_id and current["last_page"] == 7
                and current["locator"] == "消融实验 · 表三" and current["next_step"] == "核对两组的方差"
                and any(card["source_text"] == question for card in backend.paperState["cards"])
                and not backend.paperState["active"] and ui.find("paperStartFinishButton").isVisible())
            backend.workResumption.preview(bookmark_id)
            wait(lambda: not backend.workResumption.state["busy"], "restart bookmark preview")
            preview = backend.workResumption.state["preview"]
            checks["restart_bookmark_preview_preserves_next_step_without_opening"] = (
                preview["next_step"] == "核对两组的方差" and not opened)
            backend.workResumption.cancel()

            ui.filter_all_cards()
            ui.search_paper("方差实验续读")
            observations["search"] = {"allCards": ui.find("paperDesk").property("allCards"),
                "cardCount": backend.paperState["cardPage"]["total"],
                "startVisible": ui.find("paperStartFinishButton").isVisible()}
            checks["same_paper_search_clears_obsolete_all_card_filter"] = (
                not observations["search"]["allCards"] and observations["search"]["startVisible"]
                and observations["search"]["cardCount"] == 1)
            if checks["same_paper_search_clears_obsolete_all_card_filter"]:
                for width, height in ((680, 840), (520, 700)):
                    ui.chat.resize(width, height)
                    QTest.qWait(160)
                    assert ui.chat.grabWindow().save(str(output.with_name(output.stem + f"-{width}.png")))
                ui.chat.resize(680, 840)

            ui.filter_all_cards()
            shared = backend.sharedWorld
            shared.browse("papers", False, 0)
            wait(lambda: not shared.state["busy"] and shared.state["open"], "shared shelf")
            window = ui.root.findChild(QQuickWindow, "sharedWorldWindow")
            ui.click("sharedWorldRecord_0", window)
            wait(lambda: not shared.state["busy"] and bool(shared.state["detail"]), "shared paper detail")
            ui.click("sharedWorldSource", window)
            try:
                wait(lambda: not shared.state["open"], "shared route")
            except AssertionError:
                observations["sharedRouteFailure"] = shared.state
                raise
            observations["world"] = {"allCards": ui.find("paperDesk").property("allCards"),
                "startVisible": ui.find("paperStartFinishButton").isVisible()}
            checks["shared_source_reveals_actual_notebook"] = (
                not observations["world"]["allCards"] and observations["world"]["startVisible"])

            # A normal refresh is NOT a navigation request and must preserve drafts.
            ui.click("paperLibraryTab")
            ui.click("paperPositionToggle")
            ui.find("paperPageInput").setProperty("text", "11")
            backend.refreshReadingCards()
            QTest.qWait(100)
            checks["ordinary_refresh_keeps_unsaved_position"] = ui.find("paperPageInput").property("text") == "11"
            ui.search_paper("方差实验续读")
            checks["same_paper_navigation_keeps_unsaved_position"] = ui.find("paperPageInput").property("text") == "11"
            # Close the owned position dialog before continuing.
            ui.find("paperPageInput").forceActiveFocus()
            QTest.keyClick(ui.chat, Qt.Key_Escape)
            QTest.qWait(100)
            ui.click("paperLibraryTab")
            ui.click("paperStartFinishButton")
            checks["explicit_continue_uses_saved_page"] = (
                backend.paperState["active"] and backend.papers.capture_context()["page"] == 7)
            ui.filter_all_cards()
            ui.search_paper("另外一篇论文")
            checks["failed_cross_paper_navigation_preserves_session_and_view"] = (
                backend.boxSearch.state["open"] and bool(backend.boxSearch.state["error"])
                and backend.paperState["current"]["paper_id"] == paper_id
                and ui.find("paperDesk").property("allCards")
                and ui.find("paperCardSearch").property("text") == "找不到的旧筛选")
            backend.boxSearch.close()
            backend.readingFinish()
            QTest.qWait(100)
            ui.filter_all_cards()
            ui.search_paper("另外一篇论文")
            checks["successful_cross_paper_navigation_resets_view"] = (
                backend.paperState["current"]["paper_id"] == other["paper_id"]
                and not ui.find("paperDesk").property("allCards")
                and ui.find("paperStartFinishButton").isVisible())
            checks["no_automatic_external_open_or_model_request"] = not opened and all(m.calls == 0 for m in models)
            # An old card's destructive confirmation must not follow a new source.
            ui.search_paper("方差实验续读")
            ui.click("paperCardDetails_0")
            old_card_close = ui.find("paperCardClose")
            ui.click("paperCardDelete")
            old_delete_confirm = ui.find("paperDeleteConfirm")
            ui.search_paper("另外一篇论文")
            checks["source_navigation_closes_old_card_and_delete_confirmation"] = (
                not old_card_close.isVisible() and not old_delete_confirm.isVisible()
                and backend.papers.card_page(paper_id)["total"] == 1)
            ui.search_paper("方差实验续读")
            ui.click("paperOpenSourceButton")
            checks["explicit_original_uses_persisted_page_only_after_click"] = opened == [
                "https://example.org/variance.pdf#page=7"]
            ui.close()
            ui = None
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        if ui is not None:
            ui.close()
        temporary.cleanup()
        checks["no_qml_warnings_including_teardown"] = not warnings
        passed = bool(checks) and all(checks.values()) and not error
        output.write_text(json.dumps({"passed": passed, "checks": checks, "observations": observations,
            "warnings": warnings, "error": error,
            "scope": "Owned offscreen Main.qml, two Backend lifetimes, same disposable F database; no real reader, model, account or desktop input"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": passed, "checks": checks, "error": error}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
