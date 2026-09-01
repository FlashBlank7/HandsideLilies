from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# This verifier intentionally never attaches to the real desktop.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QObject, QPoint, QPointF, Property, QUrl, Qt, Signal, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConnectorBackend(QObject):
    connectorStatusChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.configure_result: dict[str, Any] = {
            "ok": False,
            "error": "缺少 Client ID",
        }
        self.oauth_result = False
        self.configure_calls = 0
        self.oauth_calls = 0

    @Property("QVariantMap", notify=connectorStatusChanged)
    def calendarStatus(self) -> dict[str, Any]:
        return {"connected": False, "policyCanonical": {}}

    @Property("QVariantMap", notify=connectorStatusChanged)
    def slackStatus(self) -> dict[str, Any]:
        return {"connected": False, "policyCanonical": {}}

    @Property("QVariantList", notify=connectorStatusChanged)
    def calendarUpcoming(self) -> list[dict[str, Any]]:
        return []

    @Property("QVariantList", notify=connectorStatusChanged)
    def slackInbox(self) -> list[dict[str, Any]]:
        return []

    @Property("QVariantMap", notify=connectorStatusChanged)
    def connectorSelectedItem(self) -> dict[str, Any]:
        return {}

    @Property("QVariantMap", notify=connectorStatusChanged)
    def connectorAssistResult(self) -> dict[str, Any]:
        return {}

    @Property("QVariantMap", notify=connectorStatusChanged)
    def connectorProposal(self) -> dict[str, Any]:
        return {}

    @Property(str, notify=connectorStatusChanged)
    def slackManifestText(self) -> str:
        return ""

    @Slot(str, "QVariantMap", result="QVariantMap")
    def connectorConfigure(self, _provider: str, _payload: object) -> dict[str, Any]:
        self.configure_calls += 1
        return dict(self.configure_result)

    @Slot(str, result=bool)
    def connectorBeginOAuth(self, _provider: str) -> bool:
        self.oauth_calls += 1
        return bool(self.oauth_result)


class WorkPanelBackend(QObject):
    workPanelAnchorRequested = Signal(str)
    stateChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.open_calls = 0
        self.connected = True

    @Property("QVariantMap", notify=stateChanged)
    def slackStatus(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "workspace": "Lilies Test",
            "policy": {},
            "policyCanonical": {},
        }

    @Property("QVariantList", notify=stateChanged)
    def slackInbox(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "event-1",
                "summary": "一条本地信笺",
                "occurredAt": "2026-09-01 09:00",
            }
        ]

    @Slot(result=bool)
    def slackOpenInbox(self) -> bool:
        self.open_calls += 1
        self.workPanelAnchorRequested.emit("slack-inbox")
        return True


def settle(app: QApplication, milliseconds: int = 100) -> None:
    app.processEvents()
    QTest.qWait(milliseconds)
    app.processEvents()


def load_window(
    engine: QQmlEngine, path: Path, backend: QObject, app: QApplication
) -> tuple[QQmlComponent, QQuickWindow]:
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
    while component.isLoading():
        settle(app, 10)
    if component.isError():
        raise RuntimeError(" | ".join(error.toString() for error in component.errors()))
    instance = component.createWithInitialProperties({"appBackend": backend})
    if not isinstance(instance, QQuickWindow):
        errors = " | ".join(error.toString() for error in component.errors())
        raise RuntimeError(f"{path.name} did not create a QQuickWindow: {errors}")
    instance.setVisible(True)
    settle(app, 180)
    # Keep the component wrapper alive for the lifetime of its unparented
    # top-level Window; otherwise PySide may collect the QML-owned instance.
    return component, instance


def visual(root: QObject, name: str) -> QQuickItem:
    item = root.findChild(QQuickItem, name)
    if item is None:
        raise RuntimeError(f"missing QML item: {name}")
    return item


def reveal_in_scroll(item: QQuickItem, scroll: QQuickItem) -> None:
    flickable = scroll.property("contentItem")
    if not isinstance(flickable, QQuickItem):
        raise RuntimeError("ScrollView has no Flickable content item")
    mapped = item.mapToItem(flickable, QPointF(0, 0))
    viewport_height = float(flickable.property("height") or scroll.height())
    content_height = float(flickable.property("contentHeight") or viewport_height)
    maximum = max(0.0, content_height - viewport_height)
    flickable.setProperty(
        "contentY", max(0.0, min(maximum, mapped.y() - viewport_height * 0.55))
    )


def click(window: QQuickWindow, item: QQuickItem, app: QApplication) -> None:
    scene = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    if scene.x() < 0 or scene.y() < 0 or scene.x() >= window.width() or scene.y() >= window.height():
        raise RuntimeError(
            f"{item.objectName()} is outside window after reveal: "
            f"{scene.x():.1f},{scene.y():.1f} in {window.width()}x{window.height()}"
        )
    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        pos=QPoint(round(scene.x()), round(scene.y())),
    )
    settle(app, 140)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    engine = QQmlEngine()

    connector_backend = ConnectorBackend()
    connector_component, connector = load_window(
        engine, PROJECT_ROOT / "qml" / "V03ConnectorSetup.qml", connector_backend, app
    )
    connector_scroll = visual(connector, "connectorSetupScroll")
    save_button = visual(connector, "connectorSaveConfigurationButton")
    reveal_in_scroll(save_button, connector_scroll)
    settle(app)
    click(connector, save_button, app)
    rejected_notice = str(connector.property("notice") or "")
    configure_rejected = (
        connector_backend.configure_calls == 1
        and rejected_notice == "缺少 Client ID"
    )

    connector_backend.configure_result = {"ok": True}
    oauth_button = visual(connector, "connectorBeginOAuthButton")
    reveal_in_scroll(oauth_button, connector_scroll)
    settle(app)
    click(connector, oauth_button, app)
    oauth_notice = str(connector.property("notice") or "")
    oauth_rejected = (
        connector_backend.configure_calls == 2
        and connector_backend.oauth_calls == 1
        and "无法启动" in oauth_notice
        and "已在" not in oauth_notice
    )
    connector.setVisible(False)
    connector.deleteLater()
    settle(app)

    work_backend = WorkPanelBackend()
    work_component, work_panel = load_window(
        engine, PROJECT_ROOT / "qml" / "V03WorkPanel.qml", work_backend, app
    )
    work_panel.setProperty("requestedSection", "connectors")
    settle(app, 240)
    work_scroll = visual(work_panel, "workPanelConnectorScroll")
    inbox_button = visual(work_panel, "workPanelSlackInboxButton")
    inbox_anchor = visual(work_panel, "workPanelSlackInboxAnchor")

    # Reproduce the signal-to-callLater race: the backend disconnects after
    # requesting the anchor but before the deferred presentation commits.
    work_backend.workPanelAnchorRequested.emit("slack-inbox")
    work_backend.connected = False
    work_backend.stateChanged.emit()
    settle(app)
    disconnected_count = int(work_panel.property("anchorPresentationCount") or 0)
    disconnected_last_anchor = str(work_panel.property("lastRevealedAnchor") or "")
    disconnected_pending_anchor = str(work_panel.property("pendingAnchor") or "")

    # Requests that originate while disconnected must also remain inert, and
    # reconnecting must not resurrect either stale request.
    work_backend.workPanelAnchorRequested.emit("slack-inbox")
    settle(app)
    rejected_count = int(work_panel.property("anchorPresentationCount") or 0)
    rejected_pending_anchor = str(work_panel.property("pendingAnchor") or "")
    work_backend.connected = True
    work_backend.stateChanged.emit()
    settle(app)
    reconnected_count = int(work_panel.property("anchorPresentationCount") or 0)
    disconnect_race_result = {
        "presentationCountAfterDisconnect": disconnected_count,
        "lastAnchorAfterDisconnect": disconnected_last_anchor,
        "pendingAnchorAfterDisconnect": disconnected_pending_anchor,
        "presentationCountAfterRejectedRequest": rejected_count,
        "pendingAnchorAfterRejectedRequest": rejected_pending_anchor,
        "presentationCountAfterReconnect": reconnected_count,
    }
    disconnect_race_guarded = (
        disconnected_count == 0
        and disconnected_last_anchor == ""
        and disconnected_pending_anchor == ""
        and rejected_count == 0
        and rejected_pending_anchor == ""
        and reconnected_count == 0
    )

    reveal_in_scroll(inbox_button, work_scroll)
    settle(app)
    click(work_panel, inbox_button, app)
    work_flickable = work_scroll.property("contentItem")
    anchor_result = {
        "backendCalls": work_backend.open_calls,
        "lastAnchor": str(work_panel.property("lastRevealedAnchor") or ""),
        "presentationCount": int(work_panel.property("anchorPresentationCount") or 0),
        "activeFocus": bool(inbox_anchor.property("activeFocus")),
        "contentY": float(work_flickable.property("contentY") or 0),
    }
    anchor_revealed = (
        anchor_result["backendCalls"] == 1
        and anchor_result["lastAnchor"] == "slack-inbox"
        and anchor_result["presentationCount"] == 1
        and anchor_result["activeFocus"]
    )

    # The compact function library routes related actions to a concrete card,
    # not merely to the broad work/growth tab.  Exercise the QML signal,
    # deferred layout turn, scroll target, focus and visible highlight for all
    # three public anchors without attaching to the real desktop.
    anchor_routes: dict[str, dict[str, object]] = {}
    route_specs = (
        ("focus", "work", "workPanelFocusCard"),
        ("reading", "work", "workPanelReadingCard"),
        ("wardrobe", "growth", "workPanelWardrobeCard"),
    )
    previous_count = int(work_panel.property("anchorPresentationCount") or 0)
    for offset, (anchor_name, section_name, object_name) in enumerate(
        route_specs, start=1
    ):
        target = visual(work_panel, object_name)
        work_backend.workPanelAnchorRequested.emit(anchor_name)
        settle(app, 180)
        anchor_routes[anchor_name] = {
            "section": str(work_panel.property("activeSection") or ""),
            "lastAnchor": str(work_panel.property("lastRevealedAnchor") or ""),
            "highlightedAnchor": str(
                work_panel.property("highlightedAnchor") or ""
            ),
            "presentationCount": int(
                work_panel.property("anchorPresentationCount") or 0
            ),
            "targetVisible": bool(target.isVisible()),
            "targetFocused": bool(target.property("activeFocus")),
            "passed": all(
                (
                    str(work_panel.property("activeSection") or "")
                    == section_name,
                    str(work_panel.property("lastRevealedAnchor") or "")
                    == anchor_name,
                    str(work_panel.property("highlightedAnchor") or "")
                    == anchor_name,
                    int(work_panel.property("anchorPresentationCount") or 0)
                    == previous_count + offset,
                    bool(target.isVisible()),
                    bool(target.property("activeFocus")),
                )
            ),
        }
    work_anchor_routes_passed = all(
        bool(value["passed"]) for value in anchor_routes.values()
    )

    work_panel.setVisible(False)
    work_panel.deleteLater()
    settle(app)
    del connector_component, work_component
    engine.deleteLater()
    settle(app)

    result = {
        "connectorObjectFailure": {
            "passed": configure_rejected,
            "notice": rejected_notice,
            "configureCalls": connector_backend.configure_calls,
        },
        "connectorBooleanFailure": {
            "passed": oauth_rejected,
            "notice": oauth_notice,
            "oauthCalls": connector_backend.oauth_calls,
        },
        "slackInboxDisconnectRace": {
            "passed": disconnect_race_guarded,
            **disconnect_race_result,
        },
        "slackInboxAnchor": {"passed": anchor_revealed, **anchor_result},
        "workPanelAnchors": {
            "passed": work_anchor_routes_passed,
            **anchor_routes,
        },
    }
    result["passed"] = all(value["passed"] for value in result.values())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
