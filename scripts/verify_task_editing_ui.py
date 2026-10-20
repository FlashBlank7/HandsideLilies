"""Task edits through real QML; all windows offscreen, no live user data."""
from __future__ import annotations

import json
import os
import tempfile

from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    output = ROOT / "artifacts" / "task-editing"
    output.mkdir(parents=True, exist_ok=True)
    checks = {}
    with tempfile.TemporaryDirectory(prefix="test-data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        engine.setInitialProperties({"appBackend": backend})
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "V03WorkPanel.qml")))

        def find(name):
            item = next((i for i in descendants(window.contentItem()) if i.objectName() == name), None)
            assert item is not None, name
            return item

        def click(name):
            item = find(name)
            assert item.isVisible() and item.isEnabled(), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            assert 0 <= point.x() <= window.width() and 0 <= point.y() <= window.height(), (name, point)
            QTest.mouseClick(window, Qt.LeftButton, pos=point.toPoint())
            QTest.qWait(70)

        try:
            assert engine.rootObjects(), warnings
            window = engine.rootObjects()[0]
            window.resize(920, 840)
            window.show()
            QTest.qWait(120)
            find("workPanelTaskTitleInput").setProperty("text", "把对照实验读明白")
            click("workPanelTaskCreateButton")
            task_id = backend.taskItems[0]["id"]
            checks["create_from_ui"] = True
            click("workPanelTaskMore_" + task_id)
            click("workPanelTaskEdit_" + task_id)
            find("taskEditorTitle").setProperty("text", "读懂实验并留下两个问题")
            backend.refreshProductivity()
            QTest.qWait(70)
            assert find("taskEditorTitle").property("text") == "读懂实验并留下两个问题"
            checks["background_refresh_keeps_editor_draft"] = True
            find("taskEditorDue").setProperty("text", "不合法日期")
            click("taskEditorSave")
            assert backend.tasks.get(task_id)["title"] == "把对照实验读明白"
            assert find("taskEditorTitle").property("text") == "读懂实验并留下两个问题"
            checks["failed_save_preserves_draft_and_original"] = True
            find("taskEditorDue").setProperty("text", "2026-10-05 18:00")
            find("taskEditorTimezone").setProperty("text", "Asia/Tokyo")
            find("taskEditorRecurrence").setProperty("currentIndex", 2)
            assert window.grabWindow().save(str(output / "task-editor.png"))
            click("taskEditorSave")
            saved = backend.tasks.get(task_id)
            assert saved["title"] == "读懂实验并留下两个问题"
            assert saved["recurrence"]["frequency"] == "weekly"
            checks["edit_deadline_recurrence_and_title"] = True
            click("workPanelTaskFocus_" + task_id)
            assert backend.focusStatus["taskId"] == task_id
            assert backend.focusStatus["taskTitle"] == saved["title"]
            assert window.grabWindow().save(str(output / "task-focus.png"))
            checks["focus_linked_to_selected_task"] = True
            backend.focusCancel()
            QTest.qWait(50)
            button = find("workPanelTaskComplete_" + task_id)
            point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, pos=point)
            QTest.qWait(40)
            QTest.mouseClick(window, Qt.LeftButton, pos=point)
            QTest.qWait(70)
            assert backend.growthStatus["points"] == 10
            assert len([v for v in backend.tasks.get(task_id)["occurrences"] if v["status"] == "completed"]) == 1
            checks["rapid_recurring_completion_does_not_complete_next_instance"] = True
            click("workPanelUndoComplete")
            assert backend.growthStatus["points"] == 0
            checks["visible_undo_compensates_exact_completion"] = True
            click("workPanelTaskMore_" + task_id)
            click("workPanelTaskArchive_" + task_id)
            assert not backend.taskItems
            find("workPanelTaskFilter").setProperty("currentIndex", 2)
            QTest.qWait(80)
            click("workPanelTaskMore_" + task_id)
            click("workPanelTaskArchive_" + task_id)
            assert backend.taskItems[0]["id"] == task_id
            assert not backend.archivedTaskItems
            assert backend.growthStatus["points"] == 0
            checks["archive_and_restore_from_ui_preserve_task_and_growth"] = True
            assert not warnings, warnings
            checks["no_qml_warnings"] = True
        finally:
            for window in app.topLevelWindows():
                window.hide()
            engine.deleteLater()
            QTest.qWait(30)
            backend.shutdown()
            app.processEvents()
    (output / "result.json").write_text(json.dumps({"checks": checks, "warnings": warnings, "passed": all(checks.values()), "environment": "offscreen; synthetic tasks; no model calls"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
