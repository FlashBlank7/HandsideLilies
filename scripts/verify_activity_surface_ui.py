"""Activity-first work surface: real QML clicks without native desktop interaction."""
from __future__ import annotations

import json
import os
import tempfile

from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    output = ROOT / "artifacts" / "activity-surface"
    output.mkdir(parents=True, exist_ok=True)
    checks, warnings = {}, []
    complete = False
    with tempfile.TemporaryDirectory(prefix="test-data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        app.setFont(QFont("Microsoft YaHei UI", 10))
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        engine.setInitialProperties({"appBackend": backend})
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "V03WorkPanel.qml")))

        def find(name):
            result = next((item for item in descendants(window.contentItem()) if item.objectName() == name), None)
            assert result is not None, name
            return result

        def click(name):
            item = find(name)
            assert item.isVisible() and item.isEnabled(), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            assert 0 <= point.x() <= window.width() and 0 <= point.y() <= window.height(), (name, point)
            QTest.mouseClick(window, Qt.LeftButton, pos=point.toPoint())
            QTest.qWait(90)

        try:
            assert engine.rootObjects(), warnings
            window = engine.rootObjects()[0]
            window.resize(520, 800)
            window.show()
            QTest.qWait(120)
            hero = find("activityHero")
            assert hero.property("mode") == "idle"
            assert not find("workPanelConnectorsTab").isVisible()
            assert not find("workPanelFocusCard").isVisible()
            assert not find("workPanelReadingCard").isVisible()
            assert not find("workPanelReminderCreateButton").isVisible()
            checks["idle_has_one_primary_action_and_optional_settings"] = True
            find("workPanelTaskTitleInput").setProperty("text", "把消融实验读完，留下两个问题")
            click("workPanelTaskCreateButton")
            task_id = backend.taskItems[0]["id"]
            assert hero.property("mode") == "task"
            assert window.grabWindow().save(str(output / "task-520.png"))
            click("activityHeroPrimary")
            assert backend.focusStatus["taskId"] == task_id
            assert hero.property("mode") == "focus"
            click("activityHeroPrimary")
            assert backend.focusStatus["paused"]
            click("activityHeroPrimary")
            assert not backend.focusStatus["paused"]
            assert window.grabWindow().save(str(output / "focus-520.png"))
            checks["primary_starts_selected_task_and_toggles_pause"] = True
            for name in ("workPanelTaskTitleInput", "workPanelTaskCreateButton", "workPanelTaskMore_" + task_id, "activityHeroSecondary"):
                item = find(name)
                left = item.mapToScene(QPointF(0, 0)).x()
                assert left >= 0 and left + item.width() <= window.width(), name
            checks["narrow_work_surface_has_no_horizontal_overflow"] = True
            backend.focusCancel()
            backend.paperAdd("对照实验里的均值与方差", "https://example.org/a.pdf")
            paper_id = backend.paperState["current"]["paper_id"]
            backend.paperSavePosition(paper_id, 7, "消融实验", "比较表三的方差")
            backend.paperLeaveQuestion(paper_id, "均值接近时怎样判断稳定性？")
            backend.paperStart(paper_id)
            QTest.qWait(100)
            assert hero.property("mode") == "reading"
            assert "1 个问题" in find("activityHeroReadingNotes").property("text")
            assert "均值接近" in find("activityHeroReadingNotes").property("text")
            assert window.grabWindow().save(str(output / "reading-520.png"))
            checks["reading_shows_paper_position_and_real_question"] = True
            requested = []
            backend.paperReviewRequested.connect(lambda: requested.append(True))
            click("activityHeroSecondary")
            assert requested and not backend.readingStatus["active"]
            checks["finish_from_work_surface_opens_marked_review"] = True
            window.setProperty("sessionControlsOpen", False)
            window.revealAnchor("focus")
            QTest.qWait(220)
            assert window.property("sessionControlsOpen")
            assert find("workPanelFocusCard").isVisible()
            assert window.property("lastRevealedAnchor") == "focus"
            checks["radial_focus_anchor_reveals_secondary_controls"] = True
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
            (output / "result.json").write_text(json.dumps({"passed": complete, "checks": checks, "warnings": warnings, "environment": "offscreen; synthetic task and paper; no model or source launch"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
