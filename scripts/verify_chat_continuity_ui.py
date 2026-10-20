"""Exercise real QML with a temporary F-drive database and offline model stub."""
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

from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts


class OfflineModel:
    ready = True
    running = False
    plan_type = "test"
    replies = 0

    def complete(self, prompt, **kwargs):
        self.replies += 1
        answer = f"第 {self.replies} 版回答：可以从上次留下的问题继续。"
        kwargs["on_delta"](answer)
        return answer

    def abort(self):
        pass

    def stop(self):
        pass


def descendants(item):
    yield item
    for child in item.childItems():
        yield from descendants(child)


def main():
    output = ROOT / "artifacts" / "chat-continuity"
    output.mkdir(parents=True, exist_ok=True)
    checks = {}
    with tempfile.TemporaryDirectory(prefix="test-data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        app.setFont(QFont("Microsoft YaHei UI", 10))
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        backend.chat._gpt = OfflineModel()
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))

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
            QTest.qWait(80)

        def settle():
            for _ in range(300):
                QTest.qWait(10)
                if not backend.chatBusy and not backend.chat._working:
                    QTest.qWait(50)
                    return
            raise AssertionError("offline model did not settle")

        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            chat = root.findChild(QQuickWindow, "chatWindow")
            old = backend.database.ensure_conversation()
            ids = []
            with backend.database.connection_session():
                for i in range(205):
                    text = "上次留下的疑问：这个实验为什么需要对照组？" if i == 55 else f"论文讨论第 {i} 句。这里是为检查界面准备的测试文字。"
                    ids.append(backend.database.add_message(old, "user" if i % 2 == 0 else "assistant", text))
            backend.setChatOpen(True)
            chat.setProperty("page", 0)
            chat.resize(680, 840)
            chat.show()
            QTest.qWait(180)
            click("chatHistoryToggle")
            find("chatHistoryInput").setProperty("text", "上次留下的疑问")
            backend.searchHistory("上次留下的疑问")
            QTest.qWait(80)
            assert len(backend.historyResults) == 1
            assert chat.grabWindow().save(str(output / "history-results.png"))
            click("chatHistoryResult_0")
            assert backend.chat.conversation_id == old
            assert not chat.property("historySearchOpen")
            expected = backend.chatPage["anchorIndex"]
            timeline = find("chatTimelineView")
            assert expected >= 0 and timeline.property("highlightedIndex") == expected
            assert not timeline.property("followTail")
            assert backend.chatPage["hasOlder"] and backend.chatPage["hasNewer"]
            checks["click_result_opens_and_positions_original_chat"] = True
            assert chat.grabWindow().save(str(output / "history-position.png"))
            first_cursor = backend.chatPage["before"]
            click("chatOlderPageButton")
            assert backend.chatPage["before"] < first_cursor
            click("chatNewerPageButton")
            assert backend.chatPage["before"] == first_cursor
            checks["paged_history_in_both_directions"] = True

            prompt = find("chatPromptInput")
            prompt.setProperty("text", "继续讨论这个实验")
            prompt.forceActiveFocus()
            QTest.keyClick(chat, Qt.Key_Return)
            settle()
            original_rows = backend.database.recent_messages(old, 500)
            assert len(original_rows) == 207
            assert original_rows[-2]["content"] == "继续讨论这个实验"
            assert not backend.chatPage["hasNewer"]
            assert backend.chatTimeline.rowCount() == 82
            assert backend.chatTimeline.data(backend.chatTimeline.index(81), backend.chatTimeline.MESSAGE_ID) == original_rows[-1]["message_id"]
            assert find("chatSaveLetter_81").isVisible() and find("chatSaveLetter_81").isEnabled()
            checks["enter_continues_same_chat_at_latest_without_duplicate"] = True
            click("chatRetryButton")
            settle()
            new_rows = backend.database.recent_messages(old, 500)
            assert len(new_rows) == 207
            assert new_rows[-1]["message_id"] == original_rows[-1]["message_id"]
            assert new_rows[-1]["content"] != original_rows[-1]["content"]
            assert backend.replyRevisions[0]["content"] == original_rows[-1]["content"]
            checks["retry_replaces_one_reply_and_preserves_original"] = True
            click("chatPreviousRepliesButton")
            assert chat.grabWindow().save(str(output / "previous-reply.png"))
            QTest.keyClick(chat, Qt.Key_Escape)
            for width in (520, 680):
                chat.resize(width, 700)
                QTest.qWait(100)
                retry = find("chatRetryButton")
                point = retry.mapToScene(QPointF(retry.width(), retry.height()))
                assert point.x() <= chat.width() and point.y() <= chat.height()
                assert chat.grabWindow().save(str(output / f"conversation-{width}.png"))
            checks["narrow_and_standard_layout"] = True
            assert not warnings, warnings
            checks["no_qml_warnings"] = True
        finally:
            for window in app.topLevelWindows():
                window.hide()
            engine.deleteLater()
            QTest.qWait(30)
            backend.shutdown()
            app.processEvents()
    (output / "result.json").write_text(json.dumps({"checks": checks, "warnings": warnings, "passed": all(checks.values()), "environment": "offscreen; synthetic history; offline model stub"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
