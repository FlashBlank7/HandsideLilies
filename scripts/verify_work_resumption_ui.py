"""Real QML clicks, synthetic windows, no desktop capture or external launches."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import threading

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QPointF, Qt, QUrl, QTimer
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(ROOT / "tests"))
from test_work_resumption import Windows


def main():
    output = ROOT / "artifacts" / "work-resumption"
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
        controller = backend.workResumption
        api = Windows()
        controller.service.api = api
        controller.service.opener = lambda item: opened.append(item["source"]) or True
        gui_thread = threading.get_ident()
        original_page = controller.service.list
        def page_in_worker(offset=0):
            assert threading.get_ident() != gui_thread, "Database listing must not run on the Qt input thread"
            return original_page(offset)
        controller.service.list = page_in_worker
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        engine.setInitialProperties({"appBackend": backend})
        engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "V03WorkPanel.qml")))

        def find(name):
            item = next((item for item in descendants(window.contentItem()) if item.objectName() == name), None)
            assert item is not None, name
            return item

        def wait():
            for _ in range(500):
                QTest.qWait(10)
                if not controller.state["busy"]:
                    QTest.qWait(80)
                    return
            raise AssertionError("background action timed out")

        def click(name):
            item = find(name)
            assert item.isVisible() and item.isEnabled(), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (name, point)
            QTest.mouseClick(window, Qt.LeftButton, pos=point.toPoint())
            wait()

        def screenshot(name):
            assert window.grabWindow().save(str(output / name))

        try:
            assert engine.rootObjects(), warnings
            window = engine.rootObjects()[0]
            window.resize(520, 800)
            window.show()
            wait()
            pulses = []
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(lambda: pulses.append(True))
            original_list = api.list_handles
            def slow_list():
                time.sleep(0.3)
                return original_list()
            api.list_handles = slow_list
            timer.start()
            click("resumptionCapture")
            timer.stop()
            api.list_handles = original_list
            assert len(pulses) >= 10
            checks["slow_native_query_does_not_block_qt_event_loop"] = True
            assert len(controller.state["draft"]["windows"]) == 2
            assert not api.restored and not opened
            find("resumptionName").setProperty("text", "读到消融实验，先去开会")
            find("resumptionNextStep").setProperty("text", "回来先看表三：两个实验组的均值接近，方差是不是也接近？")
            find("resumptionSource").setProperty("text", "https://example.org/experiment.pdf#page=7")
            click("resumptionAddSource")
            click("resumptionShowWindows")
            find("resumptionSaveScroll").property("contentItem").setProperty("contentY", 220)
            QTest.qWait(100)
            click("resumptionRefreshWindows")
            assert "回来先看表三" in find("resumptionNextStep").property("text")
            click("resumptionWindow_0")
            assert find("resumptionWindow_0").property("checked")
            assert len(find("resumptionShelf").property("selectedWindows").toVariant()) == 1
            screenshot("save-520.png")
            click("resumptionSave")
            assert not controller.state["error"], controller.state["error"]
            assert controller.state["page"]["total"] == 1
            record_id = controller.state["page"]["items"][0]["record_id"]
            assert not api.restored and not opened
            checks["explicit_save_selects_resources_and_window_without_effects"] = True

            click("resumptionLatest")
            assert "表三" in find("resumptionPreviewStep").property("text")
            assert not find("resumeResource_0").property("checked")
            assert not find("resumeWindow_0").property("checked")
            assert not api.restored and not opened
            screenshot("preview-520.png")
            checks["preview_shows_next_step_and_nothing_auto_selected"] = True
            click("resumeResource_0")
            click("resumeWindow_0")
            # A window can disappear between preview and confirmation. The UI
            # still submits only the chosen IDs; the service must revalidate.
            api.handles.remove(1)
            click("resumptionConfirm")
            result = controller.state["result"]
            assert (result["requested"], result["skipped"]) == (1, 1)
            assert opened == ["https://example.org/experiment.pdf#page=7"]
            assert not api.restored
            screenshot("result-520.png")
            checks["confirm_skips_closed_window_and_opens_only_selected_link"] = True
            # Close result dialog with Escape, then verify unavailable item.
            QTest.keyClick(window, Qt.Key_Escape)
            QTest.qWait(100)
            click("resumptionLatest")
            assert not find("resumeWindow_0").isEnabled()
            screenshot("unavailable-520.png")
            QTest.keyClick(window, Qt.Key_Escape)
            QTest.qWait(100)
            checks["unavailable_windows_are_disabled_with_reason"] = True
            click("resumptionLibrary")
            click("resumptionDelete_" + record_id)
            assert controller.state["page"]["total"] == 1
            click("resumptionDeleteConfirm")
            assert controller.state["page"]["total"] == 0
            assert opened == ["https://example.org/experiment.pdf#page=7"]
            checks["delete_requires_second_click_and_only_removes_record"] = True
            QTest.keyClick(window, Qt.Key_Escape)
            QTest.qWait(100)
            # Task association is explicit and resuming focus is a separate
            # button: neither saving nor preview automatically starts a timer.
            task = backend.tasks.create("把表三的问题写清楚")
            backend.productivityChanged.emit()
            click("resumptionCapture")
            find("resumptionName").setProperty("text", "论文里的疑问")
            find("resumptionNextStep").setProperty("text", "检查方差的定义")
            find("resumptionTaskChoice").setProperty("currentIndex", 1)
            # An unrelated refresh must not silently shift the chosen task index.
            backend.tasks.create("一个后来插入的任务", priority=3)
            backend.productivityChanged.emit()
            QTest.qWait(100)
            assert find("resumptionTaskChoice").property("currentIndex") == 1
            assert find("resumptionShelf").property("taskChoices").toVariant()[1]["id"] == task["task_id"]
            click("resumptionSave")
            assert not backend.focusStatus["active"]
            click("resumptionLatest")
            assert controller.state["preview"]["task_id"] == task["task_id"], controller.state["preview"]
            screenshot("task-preview-520.png")
            assert not backend.focusStatus["active"]
            click("resumptionFocusTask")
            assert backend.focusStatus["taskId"] == task["task_id"], backend.focusStatus
            assert opened == ["https://example.org/experiment.pdf#page=7"] and not api.restored
            checks["task_continuation_starts_focus_only_on_explicit_click"] = True
            backend.focusCancel()
            assert not warnings, warnings
            checks["no_qml_warnings"] = True
            complete = True
        finally:
            for window in app.topLevelWindows(): window.hide()
            engine.deleteLater()
            QTest.qWait(30)
            backend.shutdown()
            app.processEvents()
            (output / "result.json").write_text(json.dumps({"passed": complete, "checks": checks, "warnings": warnings,
                "environment": "offscreen; mock Windows API; all opens intercepted; no model or network"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
