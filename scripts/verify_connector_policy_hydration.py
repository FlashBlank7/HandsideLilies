from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# This verifier is never allowed to appear on the user's real desktop, even
# when an interactive Qt platform was inherited from the calling shell.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

from PySide6.QtCore import QObject, QPoint, QPointF, Property, QUrl, Qt, Signal, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConnectorPolicyBackend(QObject):
    connectorStatusChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.configure_calls: list[tuple[str, dict[str, Any]]] = []
        self._calendar_status = {
            "provider": "calendar",
            "connected": False,
            "configured": True,
            "policyCanonical": {
                "scope": "broad",
                "interruption": "priority",
                "retention": "metadata",
                "assistance": "reminder",
                "selectedSources": ["primary"],
            },
            "configuration": {"clientId": "calendar-saved-client"},
        }
        self._slack_status = {
            "provider": "slack",
            "connected": False,
            "configured": True,
            "socketReady": True,
            "policyCanonical": {
                "scope": "selected",
                "interruption": "immediate",
                "retention": "extended-cache",
                "assistance": "confirm-execute",
                "selectedSources": ["C-SAVED"],
            },
            "configuration": {
                "clientId": "slack-saved-client",
                "currentUserId": "U-SAVED",
                "redirectUri": "http://127.0.0.1:53682/oauth/callback",
            },
        }

    @Property("QVariantMap", notify=connectorStatusChanged)
    def calendarStatus(self) -> dict[str, Any]:
        return dict(self._calendar_status)

    @Property("QVariantMap", notify=connectorStatusChanged)
    def slackStatus(self) -> dict[str, Any]:
        return dict(self._slack_status)

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
    def connectorConfigure(self, provider: str, payload: object) -> dict[str, Any]:
        normalized = "calendar" if provider == "google-calendar" else str(provider)
        value = dict(payload) if isinstance(payload, dict) else {}
        self.configure_calls.append((normalized, value))
        current = dict(
            self._slack_status if normalized == "slack" else self._calendar_status
        )
        policy = dict(value.get("policy") or {})
        current["policyCanonical"] = policy
        safe = {"clientId": str(value.get("clientId", ""))}
        if normalized == "slack":
            safe.update(
                currentUserId=str(value.get("currentUserId", "")),
                redirectUri=str(value.get("redirectUri", "")),
            )
            self._slack_status = {**current, "configuration": safe}
            status = self._slack_status
        else:
            self._calendar_status = {**current, "configuration": safe}
            status = self._calendar_status
        self.connectorStatusChanged.emit()
        return {"ok": True, "status": dict(status)}


def settle(app: QApplication, milliseconds: int = 80) -> None:
    app.processEvents()
    QTest.qWait(milliseconds)
    app.processEvents()


def visual(root: QObject, name: str) -> QQuickItem:
    item = root.findChild(QQuickItem, name)
    if item is None:
        raise RuntimeError(f"missing QML item: {name}")
    return item


def reveal_and_click(
    window: QQuickWindow, item: QQuickItem, scroll: QQuickItem, app: QApplication
) -> None:
    flickable = scroll.property("contentItem")
    if not isinstance(flickable, QQuickItem):
        raise RuntimeError("ScrollView has no Flickable")
    mapped = item.mapToItem(flickable, QPointF(0, 0))
    viewport = float(flickable.property("height") or scroll.height())
    content = float(flickable.property("contentHeight") or viewport)
    flickable.setProperty(
        "contentY", max(0.0, min(max(0.0, content - viewport), mapped.y() - viewport * 0.5))
    )
    settle(app)
    scene = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        pos=QPoint(round(scene.x()), round(scene.y())),
    )
    settle(app, 120)


def text(item: QQuickItem) -> str:
    return str(item.property("text") or "")


def index(item: QQuickItem) -> int:
    return int(item.property("currentIndex"))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    engine = QQmlEngine()
    backend = ConnectorPolicyBackend()
    component = QQmlComponent(
        engine, QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "V03ConnectorSetup.qml"))
    )
    while component.isLoading():
        settle(app, 10)
    if component.isError():
        raise RuntimeError(" | ".join(error.toString() for error in component.errors()))
    # The backend can arrive after QML completion. Canonical values must replace
    # initial fallback defaults instead of becoming a stale, permanent draft.
    window = component.create()
    if not isinstance(window, QQuickWindow):
        raise RuntimeError("connector setup did not create a QQuickWindow")
    window.setProperty("appBackend", backend)
    window.setVisible(True)
    settle(app, 200)

    scope = visual(window, "connectorPolicyScope")
    interruption = visual(window, "connectorPolicyInterruption")
    retention = visual(window, "connectorPolicyRetention")
    assistance = visual(window, "connectorPolicyAssistance")
    google_client = visual(window, "connectorGoogleClientId")
    slack_client = visual(window, "connectorSlackClientId")
    slack_user = visual(window, "connectorSlackCurrentUserId")
    slack_redirect = visual(window, "connectorSlackRedirectUri")
    slack_channels = visual(window, "connectorSlackChannels")
    slack_token = visual(window, "connectorSlackXappToken")

    calendar_hydrated = (
        [index(scope), index(interruption), index(retention), index(assistance)]
        == [2, 1, 0, 0]
        and text(google_client) == "calendar-saved-client"
    )

    # Create an unsaved Calendar draft, then switch to Slack.  Slack must load
    # only its own canonical policy and public configuration.
    retention.setProperty("currentIndex", 1)
    google_client.setProperty("text", "calendar-unsaved-client")
    window.setProperty("provider", "slack")
    settle(app)
    slack_hydrated = (
        [index(scope), index(interruption), index(retention), index(assistance)]
        == [1, 2, 2, 2]
        and text(slack_client) == "slack-saved-client"
        and text(slack_user) == "U-SAVED"
        and text(slack_redirect) == "http://127.0.0.1:53682/oauth/callback"
        and text(slack_channels) == "C-SAVED"
        and text(slack_token) == ""
    )

    # Slack gets a distinct unsaved draft.  Its write-only token is never kept
    # when leaving the provider, while the non-secret edits are retained.
    scope.setProperty("currentIndex", 0)
    slack_client.setProperty("text", "slack-unsaved-client")
    slack_token.setProperty("text", "xapp-must-not-return")
    window.setProperty("provider", "calendar")
    settle(app)
    calendar_draft_restored = (
        index(retention) == 1
        and text(google_client) == "calendar-unsaved-client"
        and text(slack_token) == ""
    )
    window.setProperty("provider", "slack")
    settle(app)
    slack_draft_restored = (
        index(scope) == 0
        and text(slack_client) == "slack-unsaved-client"
        and text(slack_token) == ""
    )

    # Save a newly typed token once.  The payload may carry it into secure
    # storage, but the returned status and the UI must never fill it back in.
    slack_token.setProperty("text", "xapp-one-way")
    save_button = visual(window, "connectorSaveConfigurationButton")
    scroll = visual(window, "connectorSetupScroll")
    reveal_and_click(window, save_button, scroll, app)
    saved_provider, saved_payload = backend.configure_calls[-1]
    save_isolated = (
        saved_provider == "slack"
        and saved_payload["policy"]["scope"] == "necessary"
        and saved_payload["policy"]["retention"] == "extended-cache"
        and saved_payload["clientId"] == "slack-unsaved-client"
        and saved_payload["xappToken"] == "xapp-one-way"
        and text(slack_token) == ""
        and "xappToken" not in backend.slackStatus
        and "appToken" not in backend.slackStatus
        and "xapp-one-way" not in json.dumps(backend.slackStatus)
    )

    # A real status refresh must pick up untouched canonical fields but keep
    # independent edits. Background refreshes must not erase a token mid-entry.
    retention.setProperty("currentIndex", 1)
    slack_token.setProperty("text", "xapp-still-being-typed")
    backend._slack_status["policyCanonical"] = {
        **backend._slack_status["policyCanonical"], "interruption": "priority"
    }
    backend.connectorStatusChanged.emit()
    settle(app)
    canonical_refresh_preserves_edits = (
        index(interruption) == 1
        and index(retention) == 1
        and text(slack_token) == "xapp-still-being-typed"
        and "xapp-still-being-typed" not in json.dumps(
            window.property("slackConfigurationDraft").toVariant()
        )
    )
    window.setVisible(False)
    settle(app)
    window.setVisible(True)
    settle(app)
    reopen_keeps_draft = index(retention) == 1 and text(slack_token) == ""

    # Refresh the inactive Calendar tab too; its unsaved client/retention edits
    # survive, but an untouched scope follows the canonical saved preference.
    backend._calendar_status["policyCanonical"] = {
        **backend._calendar_status["policyCanonical"], "scope": "necessary"
    }
    backend.connectorStatusChanged.emit()
    window.setProperty("provider", "calendar")
    settle(app)
    inactive_canonical_refresh = (
        index(scope) == 0
        and index(retention) == 1
        and text(google_client) == "calendar-unsaved-client"
    )
    reveal_and_click(window, save_button, scroll, app)
    calendar_provider, calendar_payload = backend.configure_calls[-1]
    calendar_save_isolated = (
        calendar_provider == "calendar"
        and calendar_payload["policy"] == {
            "scope": "necessary",
            "interruption": "priority",
            "retention": "searchable-summary",
            "assistance": "reminder",
            "selectedSources": ["primary"],
        }
        and "xappToken" not in calendar_payload
        and "appToken" not in calendar_payload
        and backend.slackStatus["policyCanonical"]["retention"] == "extended-cache"
    )

    result = {
        "calendarHydrated": calendar_hydrated,
        "slackHydrated": slack_hydrated,
        "calendarDraftRestored": calendar_draft_restored,
        "slackDraftRestored": slack_draft_restored,
        "saveIsolated": save_isolated,
        "canonicalRefreshPreservesEdits": canonical_refresh_preserves_edits,
        "reopenKeepsDraft": reopen_keeps_draft,
        "inactiveCanonicalRefresh": inactive_canonical_refresh,
        "calendarSaveIsolated": calendar_save_isolated,
    }
    result["passed"] = all(result.values())
    print(json.dumps(result, ensure_ascii=False))
    window.setVisible(False)
    window.deleteLater()
    settle(app)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
