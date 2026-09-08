"""Render the real UI offscreen; never show a test window on the user's desktop."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_OFFSCREEN_SIZE"] = "1600x1200"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts


def main():
    output = ROOT / "artifacts" / "ui-v0355"
    output.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(prefix="lilies-v0355-") as temporary:
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
        assert engine.rootObjects(), warnings
        root = engine.rootObjects()[0]
        chat = root.findChild(QQuickWindow, "chatWindow")
        pet = root.findChild(QQuickWindow, "petWindow")
        try:
            backend.setChatOpen(True)
            backend._chat_timeline.reset_messages([
                {"role": "user", "content": "今天又存了十篇论文，一篇都没读。"},
                {"role": "assistant", "content": "收藏夹先替你读上博士了。\n挑一篇？先看它到底想回答什么。"},
                {"role": "user", "content": "你说得好像有点道理。"},
                {"role": "assistant", "content": "也可能只是我没写过开题报告，站着说话不累。"},
            ])
            # These are explicitly authored layout fixtures, not generated replies.
            chat.setProperty("page", 0)
            chat.show()
            QTest.qWait(600)
            assert chat.grabWindow().save(str(output / "conversation.png"))
            timeline = root.findChild(QQuickItem, "chatTimelineView")
            listing = root.findChild(QQuickItem, "chatMessageList")
            assert timeline and listing
            assert listing.property("count") == 4
            assert timeline.height() > 180
            assert timeline.height() >= 360, "an empty status row consumed the conversation area"
            # Inspect actual delegate geometry, not just the model row count:
            # a premature scroll can leave the tail below the viewport.
            def visual_items(item):
                yield item
                for child in item.childItems():
                    yield from visual_items(child)
            from PySide6.QtCore import QPointF
            rows = {item.objectName(): item for item in visual_items(listing)
                    if item.objectName().startswith("chatRow_")}
            geometry = {key: {"y": item.y(), "height": item.height(),
                              "viewportY": item.mapToItem(listing, QPointF()).y(),
                              "bodyHeight": item.findChild(QQuickItem, "chatMessageBody").implicitHeight()}
                        for key, item in rows.items()}
            print(json.dumps({"geometry": geometry, "contentHeight": listing.property("contentHeight"),
                              "height": listing.height(), "contentY": listing.property("contentY")}), flush=True)
            assert "chatRow_3" in rows, sorted(rows)
            tail = rows["chatRow_3"]
            tail_top = tail.mapToItem(listing, QPointF(0, 0)).y()
            assert tail_top >= 0, (tail_top, listing.height())
            assert tail_top + tail.height() <= listing.height() + 1, (
                tail_top, tail.height(), listing.height(), listing.property("contentY"))
            screenshot_paths = [str(output / "conversation.png")]
            for page, name in [(4, "settings"), (3, "companion")]:
                chat.setProperty("page", page)
                QTest.qWait(300)
                path = output / (name + ".png")
                assert chat.grabWindow().save(str(path))
                screenshot_paths.append(str(path))
            backend.setChatOpen(False)
            compact = root.findChild(QQuickItem, "desktopPet")
            compact.setProperty("expanded", True)
            QTest.qWait(400)
            assert pet.grabWindow().save(str(output / "pet-menu.png"))
            result = {"passed": not warnings, "qmlWarnings": warnings,
                      "dragMode": backend.petDragMode,
                      "chatRows": listing.property("count"),
                      "screenshots": screenshot_paths,
                      "fixtureDialogue": True}
            (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["passed"] else 1
        finally:
            for window in app.topLevelWindows(): window.hide()
            backend.shutdown()
            engine.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
