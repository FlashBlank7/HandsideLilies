"""Owned offscreen windows, isolated F-drive records; never opens a real application."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta

from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFont, QInputMethodEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    output = ROOT / "artifacts" / "box-search"
    output.mkdir(parents=True, exist_ok=True)
    checks, warnings, passed = {}, [], False
    with tempfile.TemporaryDirectory(prefix="test-data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        app.setFont(QFont("Microsoft YaHei UI", 10))
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        backend.selection.set_enabled(False)
        database = backend.database
        task = backend.tasks.create("论文方差实验", note="先核对表三的对照组")
        conversation = database.ensure_conversation()
        message = database.add_message(conversation, "user", "论文方差上次还没弄懂")
        paper = backend.papers.add("论文方差研究", "https://example.org/study")
        database.save_reading_card("variance 论文", "两组均值相近，波动仍可能不同。", title="论文阅读卡")
        database.upsert_desktop_item({"item_id": "wps-test", "name": "WPS 论文", "path": temporary + "/WPS.lnk", "source": "start-menu", "kind": "application"})
        backend.reminders.create("论文提醒", (datetime.now(UTC) + timedelta(hours=1)).isoformat())
        launched = []
        backend.desktop.launch = lambda path: launched.append((path, threading.get_ident()))
        backend.refreshReadingCards()
        backend.refreshProductivity()
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))

        def wait(predicate, reason="operation", seconds=6):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                QTest.qWait(15)
                if predicate(): return
            raise AssertionError((reason, backend.boxSearch.state, warnings,
                window.isVisible(), find(window, "boxSearchQuery").property("text")))

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
            QTest.qWait(40)

        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            window = root.findChild(QQuickWindow, "boxSearchWindow")
            chat = root.findChild(QQuickWindow, "chatWindow")
            pet = root.findChild(QQuickWindow, "petWindow")
            assert window and chat
            controller = backend.boxSearch

            def search(text, category="all"):
                if not controller.state["open"]:
                    backend.openBoxSearch()
                    wait(lambda: window.isVisible())
                window.setProperty("category", category)
                editor = find(window, "boxSearchQuery")
                editor.setProperty("text", "")
                editor.setProperty("text", text)
                window.requestActivate()
                editor.forceActiveFocus()
                wait(lambda: not controller.state["busy"] and controller.state["query"] == text)

            backend.setChatOpen(True)
            wait(lambda: chat.isVisible())
            click(chat, "chatBoxSearchButton")
            search("论文")
            assert {row["kind"] for row in controller.state["items"]} == {"app", "task", "chat", "paper", "card", "reminder"}
            window.resize(520, 600)
            QTest.qWait(80)
            assert window.grabWindow().save(str(output / "results-520.png"))
            assert not (window.flags() & Qt.WindowDoesNotAcceptFocus)
            if pet: assert pet.flags() & Qt.WindowDoesNotAcceptFocus
            checks["one_entry_six_local_providers_and_narrow_layout"] = True

            # Keyboard activation really opens the indexed app path, on the worker only.
            search("WPS", "app")
            QTest.keyClick(window, Qt.Key_Return)
            wait(lambda: not controller.state["open"])
            assert len(launched) == 1 and launched[0][0].endswith("WPS.lnk")
            assert launched[0][1] != threading.get_ident()
            checks["keyboard_app_activation_off_gui_thread"] = True

            search("论文方差上次", "chat")
            QTest.keyClick(window, Qt.Key_Return)
            wait(lambda: not controller.state["open"])
            assert backend.chat.conversation_id == conversation
            assert backend._conversation_page["anchorIndex"] >= 0
            assert any(row["message_id"] == message for row in backend._conversation_page["messages"])
            checks["exact_history_route_not_summary_only"] = True

            search("论文方差实验", "task")
            QTest.keyClick(window, Qt.Key_Return)
            wait(lambda: controller.state["preview"].get("kind") == "task")
            assert not backend.focus.status()
            click(window, "boxSearchConfirm")
            wait(lambda: not controller.state["open"])
            assert backend.focus.status()["task_id"] == task["task_id"]
            backend.focusCancel()
            checks["task_preview_starts_correct_task_focus"] = True

            search("variance", "card")
            QTest.keyClick(window, Qt.Key_Return)
            wait(lambda: controller.state["preview"].get("kind") == "card")
            assert "波动" in controller.state["preview"]["detail"]
            assert not find(window, "boxSearchConfirm").isVisible()
            assert window.grabWindow().save(str(output / "card-520.png"))
            QTest.keyClick(window, Qt.Key_Escape)
            wait(lambda: not controller.state["preview"])
            checks["reading_card_detail_and_escape_return"] = True

            search("论文方差研究", "paper")
            QTest.keyClick(window, Qt.Key_Return)
            wait(lambda: not controller.state["open"])
            assert backend.papers.selected()["paper_id"] == paper["paper_id"]
            assert chat.property("page") == 2
            checks["paper_result_opens_notebook_without_opening_external_file"] = True

            before = len(backend.reminders.list())
            search("提醒 20分钟后 喝水")
            QTest.keyClick(window, Qt.Key_Return)
            wait(lambda: controller.state["preview"].get("kind") == "new-reminder")
            assert len(backend.reminders.list()) == before
            assert window.grabWindow().save(str(output / "reminder-520.png"))
            click(window, "boxSearchConfirm")
            controller.confirm()
            wait(lambda: not controller.state["open"])
            assert len(backend.reminders.list()) == before + 1
            checks["reminder_requires_confirmation_and_double_confirm_is_inert"] = True

            search("WPS", "app")
            token = controller.state["items"][0]["id"]
            controller.search("no match", "all")
            controller.select(token)
            controller.select("shell.exec")
            wait(lambda: not controller.state["busy"])
            assert len(launched) == 1
            search("WPS", "app")
            controller._targets[controller.state["items"][0]["id"]]["expires"] = 0
            controller.select(controller.state["items"][0]["id"])
            assert "过期" in controller.state["error"]
            assert len(launched) == 1
            checks["stale_forged_expired_tokens_cannot_launch"] = True

            search("WPS", "app")
            editor = find(window, "boxSearchQuery")
            app.sendEvent(editor, QInputMethodEvent("ni", []))
            assert editor.property("inputMethodComposing")
            QTest.keyClick(window, Qt.Key_Return)
            QTest.qWait(60)
            assert len(launched) == 1
            app.sendEvent(editor, QInputMethodEvent("", []))
            checks["ime_composition_enter_does_not_activate_result"] = True

            # A slow old request must not publish into a later query or reopened palette.
            original_search = controller.service.search
            entered, release = threading.Event(), threading.Event()
            def slow(text, category, **kwargs):
                if text == "slow": entered.set(); release.wait(3)
                return original_search("论文" if text == "slow" else text, category, **kwargs)
            controller.service.search = slow
            controller.search("slow", "all")
            wait(entered.is_set)
            controller.search("WPS", "app")
            release.set()
            wait(lambda: not controller.state["busy"])
            assert all(row["kind"] == "app" for row in controller.state["items"])
            controller.service.search = original_search
            backend._companion_suppressed_status = True
            backend.habitatChanged.emit()
            wait(lambda: not controller.state["open"])
            backend.openBoxSearch()
            assert not controller.state["open"]
            backend._companion_suppressed_status = False
            backend.habitatChanged.emit()
            checks["coalesced_search_and_sensitive_state_close"] = True

            # Cancellation while an audit write is delayed must stop the queued effect.
            original_audit = database.audit
            entered, release = threading.Event(), threading.Event()
            def slow_audit(*args, **kwargs):
                entered.set(); release.wait(3)
                return original_audit(*args, **kwargs)
            database.audit = slow_audit
            search("WPS", "app")
            controller.select(controller.state["items"][0]["id"])
            wait(entered.is_set)
            controller.close()
            release.set()
            database.audit = original_audit
            search("no match", "all")
            assert len(launched) == 1
            with database.connect() as db:
                rows = [dict(row) for row in db.execute("SELECT * FROM audit_log WHERE component_id='box-search'")]
            assert len(rows) == 3 and all(row["completed_at"] for row in rows)
            assert sum(row["error_json"] is not None for row in rows) == 1
            assert not any("喝水" in row["payload_json"] or "WPS.lnk" in row["payload_json"] for row in rows)
            checks["cancellation_before_effect_and_metadata_only_audit"] = True

            backend.setBoxSearchHotkey("Ctrl+Alt+J")
            assert backend.boxSearchHotkey == "Ctrl+Alt+J"
            backend.setBoxSearchHotkey(backend.desktopPeekHotkey)
            assert backend.boxSearchHotkey == "Ctrl+Alt+J"
            backend.setDesktopPeekHotkey("Alt+Ctrl+J")
            assert backend.desktopPeekHotkey == "Ctrl+Alt+D"
            assert database.get_setting("box_search_hotkey") == "Ctrl+Alt+J"
            assert not warnings, warnings
            checks["shortcut_configuration_no_native_registration_and_no_qml_warnings"] = True
            passed = True
        finally:
            for item in app.topLevelWindows(): item.hide()
            engine.deleteLater()
            QTest.qWait(40)
            backend.shutdown()
            backend.boxSearch._thread.join(timeout=2)
            app.processEvents()
            (output / "result.json").write_text(json.dumps({"passed": passed, "checks": checks, "warnings": warnings,
                "environment": "offscreen; isolated F-drive SQLite; app launcher stub; no model, native input or network"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__": main()
