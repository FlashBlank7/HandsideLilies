"""Production QML gesture/stream overlap on offscreen, synthetic F data only."""
from __future__ import annotations

import json
import os
import tempfile
import time

from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from lilies.app import configure_quick_window_resource_lifecycle
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication


def main():
    output = ROOT / "artifacts" / "chat-drag-stream-ui"
    output.mkdir(parents=True, exist_ok=True)
    checks, warnings, passed = {}, [], False
    with tempfile.TemporaryDirectory(prefix="test-data-", dir=output) as temporary:
        os.environ["LILIES_DATA_DIR"] = temporary
        app = QApplication.instance() or QApplication([])
        load_windows_ui_fonts()
        backend = OffscreenBackend(smoke=True, force_compact=True)
        backend._v03_timer.stop()
        backend._productivity_timer.stop()
        backend.selection.set_enabled(False)
        backend.offscreen_cursor = {"x": 120, "y": 180}
        backend.offscreen_work_areas = [
            {"left": 0, "top": 0, "right": 1920, "bottom": 1040, "width": 1920, "height": 1040}
        ]
        backend._chat_timeline.begin_turn("这是一段合成的论文问题")
        backend._chat_busy = True
        backend.setChatOpen(True)
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))

        def wait(predicate, label):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if predicate():
                    return
                QTest.qWait(10)
            raise AssertionError((label, warnings))

        def call(name, *args):
            return QMetaObject.invokeMethod(
                pet, name, Q_RETURN_ARG("QVariant"),
                *(Q_ARG("QVariant", value) for value in args),
            )

        def tail_body():
            row = next((item for item in descendants(chat.contentItem()) if item.objectName() == "chatRow_1"), None)
            return next((item for item in descendants(row) if item.objectName() == "chatMessageBody"), None) if row else None

        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            pet = root.findChild(QQuickWindow, "petWindow")
            chat = root.findChild(QQuickWindow, "chatWindow")
            assert pet is not None and chat is not None
            wait(lambda: pet.isVisible() and chat.isVisible() and tail_body() is not None, "offscreen surfaces")
            QTest.qWait(250)
            hidden_panel = root.findChild(QQuickWindow, "boxWorldSceneWindow")
            assert hidden_panel is not None and not hidden_panel.isVisible()
            release_requests = []
            # Observe only our explicit cleanup request. This software/offscreen
            # check says nothing about D3D cleanup cost or presented frames.
            hidden_panel.releaseResources = lambda: release_requests.append(True)
            lifecycle = configure_quick_window_resource_lifecycle(root, backend)
            lifecycle._RELEASE_QUIET_MS = 40
            lifecycle._schedule_release(hidden_panel)
            model = backend._chat_timeline
            expected = ""
            counts = []
            for gesture in range(3):
                body = tail_body()
                changes = QSignalSpy(body.textChanged)
                serial = call("prepareCharacterGestureAtGlobal", float(pet.x() + 120), float(pet.y() + 180))
                assert int(serial) > 0 and pet.property("manualDragActive")
                assert backend._pet_pointer_critical_locked and model._stream_paused
                assert lifecycle._pointer_interaction_active
                releases_before = len(release_requests)
                # Production QML press acquires the lock; no native hook, OS
                # input or SendInput is installed/injected by this verifier.
                for part in (f"第{gesture}段", "，假设", "与观察。🌱"):
                    backend._on_chat_chunk(part)
                    expected += part
                    QTest.qWait(55)
                assert changes.count() == 0, "actual TextEdit changed during held gesture"
                assert len(release_requests) == releases_before
                assert all(not timer.isActive() for timer in lifecycle._release_timers.values())
                assert backend.chatText.endswith(expected) and backend.chatBusy
                assert call("cancelQueuedNativeCharacterPress", serial)
                assert not backend._pet_pointer_critical_locked and not pet.property("manualDragActive")
                assert not lifecycle._pointer_interaction_active
                assert changes.count() == 0
                wait(lambda: model.data(model.index(1), model.TEXT) == expected, "one resumed update")
                wait(lambda: changes.count() > 0, "document follows model")
                QTest.qWait(80)
                assert changes.count() == 1
                counts.append(changes.count())
            wait(lambda: len(release_requests) == 1, "hidden panel cleanup after release")
            checks["qml_gesture_defers_explicit_hidden_resource_cleanup"] = True
            checks["later_gestures_do_not_repeat_completed_hidden_cleanup"] = len(release_requests) == 1
            checks["qml_press_cancels_periodic_text_document_updates"] = True
            checks["three_release_cycles_each_publish_one_complete_tail"] = counts == [1, 1, 1]
            checks["unicode_and_model_busy_state_survive_presentation_pause"] = True

            serial = call("prepareCharacterGestureAtGlobal", float(pet.x() + 120), float(pet.y() + 180))
            backend._on_chat_chunk("尚未确认的草稿")
            # The real completion slot still binds committed message IDs while
            # held; cancellation/error paths must not wait for pointer release.
            backend.database.add_message(backend.chat.conversation_id, "user", "这是一段合成的论文问题")
            answer_id = backend.database.add_message(backend.chat.conversation_id, "assistant", "最终确认的解释")
            backend._on_chat_finished("最终确认的解释")
            assert not backend.chatBusy
            assert model.data(model.index(1), model.MESSAGE_ID) == answer_id
            assert model.data(model.index(1), model.TEXT) == "最终确认的解释"
            assert call("cancelQueuedNativeCharacterPress", serial)
            QTest.qWait(100)
            assert "草稿" not in backend.chatText
            checks["terminal_commit_and_source_id_are_immediate_no_stale_replay"] = True
            assert not warnings, warnings
            checks["no_qml_warnings"] = True
            passed = True
        finally:
            for window in app.topLevelWindows():
                window.hide()
            engine.deleteLater()
            QTest.qWait(40)
            backend.shutdown()
            app.processEvents()
            (output / "result.json").write_text(json.dumps({
                "passed": passed, "checks": checks, "warnings": warnings,
                "environment": "offscreen; synthetic F data and chunks; no real input/model/frame timing",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=True))


if __name__ == "__main__":
    main()
