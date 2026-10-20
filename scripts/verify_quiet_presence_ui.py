"""Causal pet motion through actual Main.qml, without touching native windows/input."""
from __future__ import annotations

import json
import os
import tempfile
import time

from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from lilies.core.pet_habitat import PetHabitatController
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    output = ROOT / "artifacts" / "quiet-presence"
    output.mkdir(parents=True, exist_ok=True)
    checks, warnings, passed = {}, [], False
    with tempfile.TemporaryDirectory(prefix="test-data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop(); backend._productivity_timer.stop()
        backend.selection.set_enabled(False)
        backend.offscreen_cursor = {"x": 30, "y": 30}
        for i in range(3):
            task = backend.tasks.create(f"合成测试事项 {i}", category="daily")
            backend.tasksComplete(task["task_id"])
        backend.wardrobeEquip("home-cardigan")
        backend.setChatOpen(False); backend.setWorkPanelOpen(False)
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
            raise AssertionError((reason, warnings))

        def find(name):
            item = next((i for i in descendants(pet.contentItem()) if i.objectName() == name), None)
            assert item is not None, name
            return item

        def snapshot(name):
            backend._set_status("")
            QTest.qWait(40)
            assert pet.grabWindow().save(str(output / (name + ".png")))

        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            pet = root.findChild(QQuickWindow, "petWindow")
            body = find("compactLilith")
            motion = find("quietPetMotion")
            book = find("quietReadingNotebook")
            wait(pet.isVisible)
            QTest.qWait(500)
            assert motion.property("acknowledgementCount") == 0  # old completion not replayed on scene creation
            assert pet.flags() & Qt.WindowDoesNotAcceptFocus
            checks["scene_creation_does_not_replay_completion_or_take_focus"] = True

            paper = backend.papers.add("用自己的节奏读这篇", "https://example.org/paper.pdf")
            backend.paperStart(paper["paper_id"])
            wait(lambda: motion.property("notebookProgress") > 0.99, "notebook opens")
            assert body.property("outfitId") == "home-cardigan" and body.property("pose") == "reading"
            assert book.isVisible() and not book.hasActiveFocus()
            wait(lambda: not body.property("poseTransitionRunning"))
            assert body.property("targetFps") == 15
            snapshot("reading-cardigan")
            checks["reading_notebook_without_outfit_swap_settles_to_15fps"] = True

            habitat = PetHabitatController(stable_seconds=0, pet_width=150, pet_height=250)
            habitat.update_foreground({"handle": 987, "appId": "synthetic-reader", "rect": {"left":180,"top":240,"right":1180,"bottom":850},
                "workArea": {"left":0,"top":0,"right":1920,"bottom":1040}, "titleBarHeight":32})
            backend._habitat_status = habitat.status()
            backend.habitatChanged.emit()
            wait(lambda: body.property("habitatLayoutActive") and not body.property("poseTransitionRunning"))
            assert motion.property("activity") == "reading" and book.isVisible()
            assert body.property("pose") == "perch-prone"
            anchor = (body.property("renderedContactX"), body.property("renderedContactY"))
            snapshot("perched-reading")
            checks["host_contact_pose_and_reading_prop_coexist"] = True

            task = backend.tasks.create("点一下，安静回应")
            backend._quiet_reaction_gate._last_at = -float("inf")
            previous_count = motion.property("acknowledgementCount")
            backend.tasksComplete(task["task_id"])
            wait(lambda: motion.property("acknowledgement") > 0.2)
            assert motion.property("acknowledgementCount") == previous_count + 1
            assert body.property("targetFps") == 60
            assert (body.property("renderedContactX"), body.property("renderedContactY")) == anchor
            backend.tasksComplete(task["task_id"])
            QTest.qWait(850)
            assert motion.property("acknowledgementCount") == previous_count + 1
            assert not motion.property("animating") and abs(motion.property("acknowledgement")) < 0.001
            assert not backend.companion.bubble.get("visible")
            checks["one_brief_completion_gesture_no_duplicate_bubble_or_anchor_move"] = True

            # A real controller gesture can be interrupted without changing the held shape.
            task = backend.tasks.create("被拖动打断的回应")
            backend._quiet_reaction_gate._last_at = -float("inf")
            backend.tasksComplete(task["task_id"])
            wait(lambda: motion.property("acknowledgement") > 0.2)
            body.setProperty("interactionSnap", True)
            held = motion.property("acknowledgement")
            QTest.qWait(200)
            assert not motion.property("animating") and motion.property("acknowledgement") == held
            old_count = motion.property("acknowledgementCount")
            # Keep the real reaction binding intact: a late completion reaches
            # the actual Main.qml gate while this rendered body is held.
            late_task = backend.tasks.create("拖动期间完成，不补播")
            backend._quiet_reaction_gate._last_at = -float("inf")
            old_sequence = backend.petReaction["sequence"]
            backend.tasksComplete(late_task["task_id"])
            QTest.qWait(40)
            assert backend.petReaction["sequence"] > old_sequence
            assert motion.property("consumedSequence") == backend.petReaction["sequence"]
            body.setProperty("interactionSnap", False)
            QTest.qWait(400)
            assert motion.property("acknowledgementCount") == old_count
            assert abs(motion.property("acknowledgement")) < 0.001
            checks["drag_freezes_response_discards_late_gesture_no_catchup"] = True

            backend._companion_suppressed_status = True
            backend.habitatChanged.emit()
            QTest.qWait(60)
            assert not book.isVisible() and not motion.property("animating")
            backend._companion_suppressed_status = False
            backend.habitatChanged.emit()
            wait(lambda: book.isVisible() and not motion.property("animating"))
            assert motion.property("acknowledgementCount") == old_count
            checks["privacy_and_game_gate_hide_prop_without_replaying_gesture"] = True

            backend.reading_sessions.finish(backend.reading_sessions.status()["session_id"])
            backend._habitat_status = PetHabitatController().status()
            backend.habitatChanged.emit(); backend.refreshProductivity()
            backend.focusStart(25); backend.focusPause()
            wait(lambda: body.property("pose") == "resting" and not body.property("poseTransitionRunning"))
            assert motion.property("restProgress") > 0.99 and not book.isVisible()
            snapshot("quiet-pause")
            backend.focusResume()
            wait(lambda: body.property("pose") == "focus-watch" and not body.property("poseTransitionRunning"))
            assert motion.property("restProgress") < 0.01
            assert body.property("outfitId") == "home-cardigan"
            backend.focusCancel()
            checks["explicit_pause_relaxes_resume_restores_activity_without_changing_clothes"] = True

            wait(lambda: not body.property("poseTransitionRunning"))
            QTest.qWait(100)
            assert body.property("targetFps") == 15
            assert not warnings, warnings
            checks["no_persistent_high_motion_and_zero_qml_warnings"] = True
            passed = True
        finally:
            for item in app.topLevelWindows(): item.hide()
            engine.deleteLater(); QTest.qWait(40)
            backend.shutdown(); app.processEvents()
            (output / "result.json").write_text(json.dumps({"passed":passed,"checks":checks,"warnings":warnings,
                "environment":"offscreen; F-drive synthetic records and host; no live desktop/model/input"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=True))


if __name__ == "__main__": main()
