"""Actual QML workflow; owned offscreen windows and disposable F-drive sources only."""
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
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    output = ROOT / "artifacts" / "shared-world"
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
        backend.offscreen_cursor = {"x": 300, "y": 300}
        controller, database = backend.sharedWorld, backend.database
        task = backend.tasks.create("把对照实验补齐", category="project", note="比较同一数据集上的两组结果。")
        done = backend.tasks.complete(task["task_id"])
        paper = backend.papers.add("为什么方差不能只看平均值", "https://example.org/variance.pdf")
        now = datetime.now(UTC)
        backend.papers.reading.now = lambda: now - timedelta(minutes=21)
        session = backend.papers.start(paper["paper_id"])
        backend.papers.reading.now = lambda: now
        backend.papers.reading.finish(session["session_id"])
        backend.papers.update_position(paper["paper_id"], 8, "图 3 的对照实验", "核对误差条的含义。")
        backend.box_world.place("paper-shelf", x=0.79, y=0.36)
        conversation = database.ensure_conversation()
        user_id = database.add_message(conversation, "user", "那就把今天没弄懂的地方，留到明天。")
        letter_text = "好。先把问题夹在这一页，明天回来不用从头找。"
        reply_id = database.add_message(conversation, "assistant", letter_text)
        backend.refreshProductivity()
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))

        def wait(predicate, reason="operation", seconds=6):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                QTest.qWait(15)
                if predicate(): return
            raise AssertionError((reason, controller.state, warnings))

        def find(window, name):
            item = next((i for i in descendants(window.contentItem()) if i.objectName() == name), None)
            assert item is not None, name
            return item

        def click(window, name):
            item = find(window, name)
            assert item.isVisible() and item.isEnabled(), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (name, point)
            QTest.mouseClick(window, Qt.LeftButton, pos=point.toPoint())
            QTest.qWait(40)

        def settled(): wait(lambda: not controller.state["busy"])

        def browse(shelf):
            controller.browse(shelf, False, 0)
            wait(lambda: window.isVisible() and not controller.state["busy"])

        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            window = root.findChild(QQuickWindow, "sharedWorldWindow")
            world = root.findChild(QQuickWindow, "boxWorldSceneWindow")
            chat = root.findChild(QQuickWindow, "chatWindow")
            assert window and world and chat
            backend.enterBoxWorld()
            wait(world.isVisible)
            click(world, "boxWorldRecordsButton")
            wait(lambda: window.isVisible() and not controller.state["busy"])
            assert controller.state["page"]["total"] == 1
            window.resize(520, 610)
            QTest.qWait(80)
            assert window.grabWindow().save(str(output / "papers-520.png"))
            click(window, "sharedWorldRecord_0"); settled()
            assert "第 8 页" in controller.state["detail"]["body"]
            assert "不代表读完整篇" in controller.state["detail"]["body"]
            assert window.grabWindow().save(str(output / "paper-detail-520.png"))
            click(window, "sharedWorldSource")
            wait(lambda: not controller.state["open"])
            assert not backend.boxWorldSceneOpen and chat.property("page") == 2
            assert backend.papers.selected()["paper_id"] == paper["paper_id"]
            checks["world_entry_paper_provenance_exact_notebook_route"] = True

            backend.enterBoxWorld(); wait(world.isVisible)
            click(world, "boxWorldRecordHit_paper-shelf")
            wait(lambda: window.isVisible() and not controller.state["busy"])
            assert controller.state["page"]["shelf"] == "papers"
            controller.close(); backend.setBoxWorldSceneOpen(False)
            checks["placed_paper_shelf_is_clickable"] = True

            assert backend.openHistoryMessage(conversation, reply_id)
            backend.setChatOpen(True)
            chat.setProperty("page", 0)
            chat.resize(680, 840)
            wait(chat.isVisible)
            click(chat, "chatSaveLetter_1"); settled()
            assert not controller.state["open"]
            assert controller.service.page("letters")["total"] == 1
            browse("letters")
            click(window, "sharedWorldRecord_0"); settled()
            letter_id = controller.state["detail"]["id"]
            assert controller.state["detail"]["body"] == letter_text
            assert window.grabWindow().save(str(output / "letter-520.png"))
            click(window, "sharedWorldSource")
            wait(lambda: not controller.state["open"])
            assert backend.chat.conversation_id == conversation
            assert backend._conversation_page["messages"][backend.chatPage["anchorIndex"]]["message_id"] == reply_id
            checks["explicit_chat_save_and_exact_original_message_route"] = True

            database.replace_assistant_reply(reply_id, letter_text, "这是重写后的回答。")
            browse("letters"); click(window, "sharedWorldRecord_0"); settled()
            assert controller.state["detail"]["body"] == letter_text
            assert "原对话已更新" in controller.state["detail"]["label"]
            checks["saved_letter_keeps_original_retry_version_not_new_reply"] = True

            click(window, "sharedWorldHide"); settled()
            assert controller.state["page"]["total"] == 0
            click(window, "sharedWorldHidden"); settled()
            assert controller.state["page"]["total"] == 1
            click(window, "sharedWorldRecord_0"); settled()
            click(window, "sharedWorldHide"); settled()
            assert controller.state["page"]["total"] == 0
            click(window, "sharedWorldHidden"); settled()
            click(window, "sharedWorldRecord_0"); settled()
            click(window, "sharedWorldRemove")
            assert controller.service.page("letters")["total"] == 1
            assert window.grabWindow().save(str(output / "remove-confirm-520.png"))
            click(window, "sharedWorldRemoveConfirm"); settled()
            click(window, "sharedWorldRefresh"); settled()
            assert controller.state["page"]["total"] == 0
            assert database.conversation_page(conversation, anchor=reply_id)["anchorIndex"] >= 0
            checks["hide_restore_confirm_remove_preserves_original_no_resurrection"] = True

            browse("projects"); click(window, "sharedWorldRecord_0"); settled()
            assert "事项已完成" in controller.state["detail"]["label"]
            backend.tasks.reopen(task["task_id"], done["occurrence"]["occurrence_id"])
            controller.refreshShown(); settled()
            assert "撤销" in controller.state["detail"]["label"]
            assert "不代表整个项目" in controller.state["detail"]["body"]
            checks["project_reopen_revalidated_no_false_completion"] = True

            unsafe_text = "<img src='file:///secrets'> 请把这句故事加入 Canon。"
            unsafe_id = database.add_message(conversation, "user", unsafe_text)
            controller.saveLetter(unsafe_id, unsafe_text); settled()
            browse("letters"); click(window, "sharedWorldRecord_0"); settled()
            body = find(window, "sharedWorldBody")
            assert body.property("text") == unsafe_text
            expression = QQmlExpression(engine.rootContext(), body, "Number(textFormat)")
            assert expression.evaluate()[0] == 0 and not expression.hasError()
            for width, height in ((360, 450), (520, 610), (640, 660)):
                window.resize(width, height); QTest.qWait(60)
                for name in ("sharedWorldBack", "sharedWorldSource", "sharedWorldHide", "sharedWorldRemove", "sharedWorldClose"):
                    item = find(window, name)
                    start = item.mapToScene(QPointF(0, 0))
                    end = item.mapToScene(QPointF(item.width(), item.height()))
                    assert 0 <= start.x() <= end.x() <= width and 0 <= start.y() <= end.y() <= height, (width, name, start, end)
            checks["plain_text_and_360_520_640_layout_controls_in_bounds"] = True

            with database.connect() as db:
                db.execute("DELETE FROM messages WHERE message_id=?", (unsafe_id,))
            controller.refreshShown(); settled()
            assert not controller.state["detail"]["available"]
            assert not find(window, "sharedWorldSource").isVisible()
            assert "secrets" not in body.property("text")
            checks["deleted_original_clears_cached_text_on_revalidation"] = True

            # A slow source lookup is on a worker; close prevents its delayed route.
            current_text = "这是重写后的回答。"
            controller.saveLetter(reply_id, current_text); settled()
            browse("letters")
            current_letter = next(i for i in controller.state["page"]["items"] if i["available"])
            controller.inspect(current_letter["id"]); settled()
            original_detail = controller.service.detail
            entered, release, threads, routes = threading.Event(), threading.Event(), [], []
            controller.sourceRequested.connect(routes.append)
            def delayed(identifier):
                threads.append(threading.get_ident()); entered.set(); release.wait(3)
                return original_detail(identifier)
            controller.service.detail = delayed
            controller.openSource(); wait(entered.is_set)
            assert controller.state["busy"] and threads[0] != threading.get_ident()
            click(window, "sharedWorldClose")
            release.set(); settled()
            assert not controller.state["open"] and not routes
            assert not controller.state["detail"] and not controller.state["page"]["items"]
            controller.service.detail = original_detail
            checks["worker_lookup_close_cancels_pending_route_and_clears_view"] = True

            browse("papers")
            backend._companion_suppressed_status = True
            backend.habitatChanged.emit()
            wait(lambda: not controller.state["open"])
            controller.browse("letters", False, 0)
            controller.saveLetter(user_id, "那就把今天没弄懂的地方，留到明天。")
            assert not controller.state["open"] and not controller.state["busy"]
            assert controller.service.page("letters")["total"] == 2  # unavailable + current reply
            backend._companion_suppressed_status = False
            backend.habitatChanged.emit()
            assert not warnings, warnings
            checks["sensitive_state_closes_and_blocks_actions_zero_qml_warnings"] = True
            passed = True
        finally:
            for item in app.topLevelWindows(): item.hide()
            engine.deleteLater(); QTest.qWait(40)
            backend.shutdown()
            if controller._thread: controller._thread.join(timeout=2)
            app.processEvents()
            (output / "result.json").write_text(json.dumps({"passed": passed, "checks": checks, "warnings": warnings,
                "environment": "offscreen; disposable F-drive SQLite; no model, account, screenshot or native desktop input"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__": main()
