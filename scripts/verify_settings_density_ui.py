"""Settings disclosure and minimum-size records, entirely offscreen/synthetic."""
from __future__ import annotations

import json
import os
import tempfile
import time

from verify_chat_continuity_ui import ROOT, descendants
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    assert scale in {"1", "2"}
    output = ROOT / "artifacts" / ("settings-density-" + scale)
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
        probes = []
        # Consent UI may reach the real local authorization setter below, but
        # capability discovery must never contact the subscription bridge.
        backend.companionService._probe_modalities = lambda: probes.append("fixture-only")
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
        engine.rootContext().setContextProperty("backend", backend)
        engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
        engine.load(str(ROOT / "qml" / "Main.qml"))

        def find(window, name):
            item = next((item for item in descendants(window.contentItem()) if item.objectName() == name), None)
            assert item is not None, name
            return item

        def bounds(window, item):
            start = item.mapToScene(QPointF())
            end = item.mapToScene(QPointF(item.width(), item.height()))
            assert item.isVisible() and item.width() > 0 and item.height() > 0, item.objectName()
            assert 0 <= start.x() < end.x() <= window.width() + 1, (item.objectName(), start, end)
            assert 0 <= start.y() < end.y() <= window.height() + 1, (item.objectName(), start, end)

        def click(window, name):
            item = find(window, name)
            bounds(window, item)
            assert item.isEnabled(), name
            center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
            QTest.mouseClick(window, Qt.LeftButton, pos=center.toPoint())
            QTest.qWait(70)

        def settled(controller):
            until = time.monotonic() + 6
            while controller.state["busy"] and time.monotonic() < until:
                QTest.qWait(15)
            assert not controller.state["busy"], "synthetic records worker did not finish"

        def capture(window, name):
            QTest.qWait(60)
            assert window.grabWindow().save(str(output / (name + ".png")))

        try:
            assert engine.rootObjects(), warnings
            root = engine.rootObjects()[0]
            chat = root.findChild(QQuickWindow, "chatWindow")
            world = root.findChild(QQuickWindow, "sharedWorldWindow")
            backend.setChatOpen(True)
            chat.setProperty("page", 3)
            find(chat, "companionNavigation").setProperty("section", "observation")
            chat.show()
            QTest.qWait(130)
            details = find(chat, "companionObservationDetails")
            for width, height in ((520, 540), (520, 700), (680, 840)):
                chat.resize(width, height)
                QTest.qWait(70)
                assert not details.isVisible()
                for name in ("companionObservationSummary", "companionObservationRecentContext",
                             "companionObservationPauseSwitch", "companionObservationConsentButton"):
                    bounds(chat, find(chat, name))
                capture(chat, f"observation-{width}-{height}")
            checks["status_context_and_controls_above_fold_at_minimum_size"] = True

            assert not backend.companionService.activityStatus["smartObservationEnabled"]
            before = backend.companionService.preferences
            click(chat, "companionObservationDetailsToggle")
            assert details.isVisible()
            assert find(chat, "companionModalityRecheckButton").isVisible()
            capture(chat, "details")
            click(chat, "companionObservationDetailsToggle")
            assert not details.isVisible()
            assert backend.companionService.preferences == before
            assert not backend.companionService.activityStatus["smartObservationEnabled"]
            checks["disclosure_preserves_settings_and_evidence_controls"] = True

            # Display the actual consent, then cancel: no model or capture.
            chat.resize(520, 540)
            QTest.qWait(70)
            click(chat, "companionObservationConsentButton")
            consent = root.findChild(QObject, "smartObservationConfirmDialog")
            assert consent.property("opened")
            for name in ("smartObservationDeclineButton", "smartObservationAcceptButton"):
                bounds(chat, find(chat, name))
            assert find(chat, "smartObservationConsentScroll").height() >= 100
            capture(chat, "consent-minimum")
            text = str(find(chat, "smartObservationConsentText").property("text"))
            assert "规则识别仍有边界" in text and "失败或取消后都会删除" in text
            assert not backend.companionService.activityStatus["smartObservationEnabled"]
            viewport = find(chat, "smartObservationConsentScroll").property("contentItem")
            viewport.setProperty("contentY", max(0, viewport.property("contentHeight") - viewport.height()))
            capture(chat, "consent-end")
            bounds(chat, find(chat, "smartObservationAcceptButton"))
            QTest.keyClick(chat, Qt.Key_Escape)
            QTest.qWait(70)
            assert not consent.property("opened")
            assert not backend.companionService.activityStatus["smartObservationEnabled"]
            assert not probes
            click(chat, "companionObservationConsentButton")
            assert consent.property("opened") and viewport.property("contentY") == 0
            click(chat, "smartObservationDeclineButton")
            assert not consent.property("opened") and not probes
            click(chat, "companionObservationConsentButton")
            click(chat, "smartObservationAcceptButton")
            assert backend.companionService.activityStatus["smartObservationEnabled"]
            assert backend.database.get_setting("smart_observation_authorized", False) is True
            assert probes == ["fixture-only"]
            click(chat, "companionObservationConsentButton")
            assert not backend.companionService.activityStatus["smartObservationEnabled"]
            assert backend.database.get_setting("smart_observation_authorized", True) is False
            click(chat, "companionObservationPauseSwitch")
            assert backend.companionService.activityStatus["paused"]
            click(chat, "companionObservationPauseSwitch")
            assert not backend.companionService.activityStatus["paused"]
            checks["consent_requires_confirmation_and_pause_is_explicit"] = True

            backend.companionService._last_capture_status.update(outcome="failed", reason="image-model-unavailable")
            backend.companionService.changed.emit()
            QTest.qWait(70)
            failure = find(chat, "companionObservationFailure")
            assert failure.isVisible() and not details.isVisible()
            assert "最近观察未完成" in failure.property("text") and "暂无图像模型" in failure.property("text")
            checks["failed_observation_is_not_hidden_by_disclosure"] = True

            # A real recorded paper with deliberately long user-provided title.
            title = "关于长标题在阅读回顾中是否仍能看清出处和继续阅读入口的研究" * 9
            paper = backend.papers.add(title, "https://example.org/synthetic-paper.pdf")
            session = backend.papers.start(paper["paper_id"])
            backend.papers.reading.finish(session["session_id"])
            backend.papers.update_position(paper["paper_id"], 12, "对照实验" * 18, "下次核对假设。")
            controller = backend.sharedWorld
            controller.browse("papers", False, 0)
            settled(controller)
            assert len(controller.state["page"]["items"]) == 1
            controller.inspect(controller.state["page"]["items"][0]["id"])
            settled(controller)
            for width, height in ((360, 360), (360, 450), (520, 610)):
                world.resize(width, height)
                QTest.qWait(90)
                for name in ("sharedWorldClose", "sharedWorldBack", "sharedWorldSource", "sharedWorldHide", "sharedWorldRemove"):
                    bounds(world, find(world, name))
                viewport = find(world, "sharedWorldDetailScroll")
                assert viewport.property("availableHeight") >= 50, (width, height, viewport.height())
                capture(world, f"long-paper-{width}-{height}")
            checks["long_record_title_preserves_readable_body_and_all_actions"] = True
            assert not warnings, warnings
            passed = True
        finally:
            for window in app.topLevelWindows():
                window.hide()
            engine.deleteLater()
            QTest.qWait(40)
            backend.shutdown()
            if backend.sharedWorld._thread:
                backend.sharedWorld._thread.join(timeout=2)
            app.processEvents()
            report = {"passed": passed, "checks": checks, "warnings": warnings, "scale": scale,
                      "scope": "Offscreen only; synthetic F-drive data, no model/network/desktop capture or native input"}
            (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
