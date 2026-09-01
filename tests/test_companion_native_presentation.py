from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication, QWindow

from lilies.app import CompanionBubblePresentationProbe


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _SyntheticWindow(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.visible = True
        self.exposed = False
        self.window_visibility = QWindow.Visibility.Windowed
        self.setProperty("presentationRevision", 4)

    def isVisible(self) -> bool:
        return self.visible

    def isExposed(self) -> bool:
        return self.exposed

    def visibility(self) -> QWindow.Visibility:
        return self.window_visibility


class _SyntheticController(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, bool, bool, int]] = []

    def ackPresented(
        self, bubble_id: str, visible: bool, exposed: bool, revision: int
    ) -> bool:
        self.calls.append((bubble_id, visible, exposed, revision))
        return True


def test_native_probe_acks_only_an_exposed_non_hidden_window() -> None:
    # A QCoreApplication cannot later be upgraded to QGuiApplication in the
    # same pytest process.  The companion QML lifecycle tests intentionally
    # share that process, so establish the stronger application type here.
    app = QGuiApplication.instance() or QGuiApplication([])
    parent = QObject()
    window = _SyntheticWindow()
    controller = _SyntheticController()
    probe = CompanionBubblePresentationProbe(window, controller, parent)
    probe._RETRY_DELAYS_MS = (0,)

    probe.requestAck("content-free-id", 4)
    app.processEvents()
    assert controller.calls == []

    window.exposed = True
    window.setProperty("presentationRevision", 5)
    probe.requestAck("content-free-id", 5)
    probe.cancelPending()
    app.processEvents()
    assert controller.calls == []

    probe.requestAck("content-free-id", 5)
    app.processEvents()
    assert controller.calls == [("content-free-id", True, True, 5)]

    # A delayed callback from an older presentation turn must not acknowledge
    # the current bubble window even if it is now exposed.
    window.setProperty("presentationRevision", 7)
    probe.requestAck("content-free-id", 6)
    app.processEvents()
    assert controller.calls == [("content-free-id", True, True, 5)]

    window.window_visibility = QWindow.Visibility.Hidden
    probe.requestAck("content-free-id", 7)
    app.processEvents()
    assert controller.calls == [("content-free-id", True, True, 5)]


def test_qml_routes_production_ack_through_native_probe_only() -> None:
    source = (PROJECT_ROOT / "qml" / "CompanionBubble.qml").read_text("utf-8")
    app_source = (PROJECT_ROOT / "src" / "lilies" / "app.py").read_text("utf-8")

    assert "property var nativePresentationController: null" in source
    assert "nativePresentationController.requestAck(" in source
    assert "nativePresentationController.cancelPending()" in source
    assert 'Qt.platform.pluginName || ""' in source
    assert '=== "offscreen"' in source
    assert 'QQuickWindow, "companionBubbleWindow"' in app_source
    assert "CompanionBubblePresentationProbe(" in app_source
    assert '"nativePresentationController", companion_presentation_probe' in app_source


def test_tray_and_settings_recovery_surfaces_are_content_free() -> None:
    app_source = (PROJECT_ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    main_source = (PROJECT_ROOT / "qml" / "Main.qml").read_text("utf-8")
    tray_refresh = app_source.split("def refresh_tray_status() -> None:", 1)[1].split(
        "backend.habitatChanged.connect", 1
    )[0]

    assert 'unread_companion.setText(f"未读陪伴：{unread_count}")' in tray_refresh
    assert "summary" not in tray_refresh
    assert ".bubble" not in tray_refresh
    assert 'objectName: "companionDeliveryStatusLabel"' in main_source
    assert 'objectName: "companionReopenUnreadButton"' in main_source
    assert 'text: "重新显示未读"' in main_source
    assert 'objectName: "companionMarkUnreadReadButton"' in main_source
    assert 'text: "标记已读"' in main_source
    assert "backend.companionService.markUnreadRead()" in main_source
    assert "不显示气泡正文或窗口标题" in main_source
