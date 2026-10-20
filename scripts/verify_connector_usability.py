"""Exercise the real connector QML with isolated, no-network example content."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
os.environ["QML_DISABLE_DISK_CACHE"] = "1"

from PySide6.QtCore import QObject, Property, QUrl, Qt, Slot
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from verify_connector_policy_hydration import (
    ConnectorPolicyBackend, PROJECT_ROOT, reveal_and_click, settle, text, visual,
)


class UsabilityBackend(ConnectorPolicyBackend):
    def __init__(self):
        super().__init__()
        for status in (self._calendar_status, self._slack_status):
            status.update(connected=False, configured=False, state="not-configured")
            status["policyCanonical"].update(retention="metadata", assistance="assist")
        self.items = []
        self.selection = {}
        self.proposal = {}
        self.assist_calls = []
        self.confirm_calls = []
        self.oauth_calls = []
        self.open_calls = []
        self.destructive_calls = []

    @Property("QVariantList", notify=ConnectorPolicyBackend.connectorStatusChanged)
    def slackInbox(self):
        return self.items

    @Property("QVariantMap", notify=ConnectorPolicyBackend.connectorStatusChanged)
    def connectorSelectedItem(self):
        return self.selection

    @Property("QVariantMap", notify=ConnectorPolicyBackend.connectorStatusChanged)
    def connectorProposal(self):
        return self.proposal

    @Slot(str, str, result=bool)
    def connectorSelectItem(self, provider, identifier):
        self.selection = {"provider": provider, "id": identifier}
        self.connectorStatusChanged.emit()
        return True

    @Slot(str, str, str, result=bool)
    def connectorAssist(self, provider, identifier, instruction):
        self.assist_calls.append((provider, identifier, instruction))
        return True

    @Slot(str, str, result="QVariantMap")
    def slackProposeReply(self, identifier, body):
        self.proposal = {"id": "preview-one", "connector": "slack", "action": "reply",
                         "target": "示例工作区 / 原线程", "before": {}, "after": {"text": body},
                         "expiresAt": "2026-10-03T20:00:00+09:00", "executing": False}
        self.connectorStatusChanged.emit()
        return {"ok": True, "proposal": self.proposal}

    @Slot(str, str, result=bool)
    def connectorConfirmProposal(self, provider, identifier):
        if self.proposal.get("executing"):
            return False
        self.confirm_calls.append((provider, identifier, self.proposal["after"]["text"]))
        self.proposal = {**self.proposal, "executing": True}
        self.connectorStatusChanged.emit()
        return True

    @Slot(str, result=bool)
    def connectorBeginOAuth(self, provider):
        self.oauth_calls.append(provider)
        return True

    @Slot(str, result=bool)
    def slackOpenMessage(self, identifier):
        self.open_calls.append(identifier)
        return True

    @Slot(str, result=bool)
    def connectorClearContent(self, provider):
        self.destructive_calls.append(("clear", provider))
        return True

    @Slot(str, result=bool)
    def connectorDisconnect(self, provider):
        self.destructive_calls.append(("disconnect", provider))
        return True


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "artifacts" / "connector-usability"
    if output.drive.casefold() != "f:":
        raise ValueError("UI artifacts must stay on F drive")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "segoeui.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei", 10))
    assert QFontMetrics(app.font()).inFontUcs4(ord("莉")), "offscreen CJK font is unavailable"
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
    backend = UsabilityBackend()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "V03ConnectorSetup.qml")))
    while component.isLoading():
        settle(app, 10)
    assert not component.isError(), [error.toString() for error in component.errors()]
    window = component.createWithInitialProperties({"appBackend": backend})
    assert isinstance(window, QQuickWindow)
    window.resize(520, 760)
    window.show()
    settle(app, 160)
    scroll = visual(window, "connectorSetupScroll")

    def click(name):
        reveal_and_click(window, visual(window, name), scroll, app)

    def capture(name):
        settle(app, 80)
        assert window.grabWindow().save(str(output / (name + ".png")))

    def fitted(name):
        item = visual(window, name)
        assert item.width() > 0
        assert item.width() <= window.width() - 24, name

    checks = []
    assert visual(window, "connectorOptionalIntro").isVisible()
    assert not visual(window, "connectorGoogleSetupCard").isVisible()
    assert not visual(window, "connectorCalendarCreateCard").isVisible()
    assert not visual(window, "connectorSaveConfigurationButton").isVisible()
    capture("optional-520")
    checks.append("disconnected optional overview, no automatic setup or forms")

    click("connectorStartSetup")
    assert visual(window, "connectorGoogleSetupCard").isVisible()
    visual(window, "connectorGoogleClientId").setProperty("text", "example-draft")
    click("connectorUseTab")
    click("connectorSettingsTab")
    assert text(visual(window, "connectorGoogleClientId")) == "example-draft"
    for width in (480, 520, 680):
        window.resize(width, 760)
        settle(app)
        for control in ("connectorSaveConfigurationButton", "connectorBeginOAuthButton", "connectorGoogleClientId"):
            fitted(control)
        capture(f"settings-{width}")
    checks.append("settings separated, draft retained, 480/520/680px layout")

    window.resize(520, 760)
    window.setProperty("provider", "slack")
    visual(window, "connectorSlackXappToken").setProperty("text", "fake-token-not-to-be-restored")
    click("connectorUseTab")
    assert text(visual(window, "connectorSlackXappToken")) == ""
    backend.items = [{"id": "one", "sourceId": "C-EXAMPLE", "link": "https://example.org/message",
                      "summary": "一起看看这份草稿", "assistanceState": "metadata-only"}]
    backend._slack_status.update(connected=True, state="connected", displayName="示例账号")
    backend.connectorStatusChanged.emit()
    settle(app)
    assert text(visual(window, "connectorAccountLabel")) == "示例账号"
    click("connectorChooseItem_0")
    instruction = visual(window, "connectorSlackAssistInstruction")
    instruction.setProperty("text", "帮我拟一条简短回复")
    settle(app)
    assert visual(window, "connectorSlackItemCard").isVisible()
    assert not visual(window, "connectorSlackAssistButton").isEnabled()
    assert "不保存正文" in text(visual(window, "connectorSlackAssistExplanation"))
    assert backend.open_calls == []
    click("connectorOpenOriginal")
    assert backend.open_calls == ["one"]
    capture("metadata-520")
    checks.append("explicit item selection, metadata tier unavailable, secret cleared on leaving settings")

    backend._slack_status["policyCanonical"].update(retention="extended-cache", assistance="confirm-execute")
    backend.items[0]["assistanceState"] = "content-unavailable"
    backend.connectorStatusChanged.emit()
    settle(app)
    assert not visual(window, "connectorSlackAssistButton").isEnabled()
    assert "不会补回旧内容" in text(visual(window, "connectorSlackAssistExplanation"))
    capture("missing-content-520")
    backend.items[0].update(text="请看看这份草稿，明天再一起讨论。", assistanceState="available")
    backend._slack_status.update(connected=False, state="configured", error="示例离线状态")
    backend.connectorStatusChanged.emit()
    settle(app)
    assert text(visual(window, "connectorConnectionState")) == "连接异常"
    assert visual(window, "connectorSlackAssistButton").isEnabled(), "local retained text does not require a live connector"
    click("connectorSlackAssistButton")
    assert backend.assist_calls == [("slack", "one", "帮我拟一条简短回复")]
    checks.append("old content stays unavailable after retention upgrade; offline retained item can be assisted")

    click("connectorSettingsTab")
    visual(window, "connectorSlackClientId").setProperty("text", "unsaved-client")
    visual(window, "connectorSlackXappToken").setProperty("text", "fake-mid-entry")
    backend.connectorStatusChanged.emit()
    settle(app)
    assert window.property("section") == "settings", "background status must not navigate or steal focus"
    assert text(visual(window, "connectorSlackClientId")) == "unsaved-client"
    assert text(visual(window, "connectorSlackXappToken")) == "fake-mid-entry"
    assert backend.configure_calls == [] and backend.oauth_calls == []
    click("connectorUseTab")
    backend.items[0].pop("assistanceState")
    backend.connectorStatusChanged.emit()
    settle(app)
    assert not visual(window, "connectorSlackAssistButton").isEnabled(), "unknown readiness must fail closed"
    checks.append("background refresh preserves page/draft; unknown availability fails closed")

    visual(window, "connectorSlackReplyText").setProperty("text", "好的，明天一起看。")
    click("connectorSlackProposeButton")
    assert backend.confirm_calls == []
    assert visual(window, "connectorProposalCard").isVisible()
    capture("preview-520")
    click("connectorSettingsTab")
    assert not visual(window, "connectorProposalCard").isVisible()
    click("connectorUseTab")
    assert backend.confirm_calls == []
    click("connectorConfirmProposalButton")
    assert backend.confirm_calls == [("slack", "preview-one", "好的，明天一起看。")]
    assert not visual(window, "connectorConfirmProposalButton").isEnabled()
    checks.append("preview/navigation never submits; explicit confirm sends exact text once")

    click("connectorSettingsTab")
    window.resize(480, 760)
    for button, dialog_name in (("connectorClearContentButton", "connectorClearContentDialog"),
                               ("connectorDisconnectButton", "connectorDisconnectDialog")):
        click(button)
        dialog = window.findChild(QObject, dialog_name)
        assert dialog is not None and dialog.property("visible")
        assert dialog.property("height") <= window.height() - 24
        assert window.activeFocusItem().property("text") == "取消"
        capture(dialog_name + "-480")
        QTest.keyClick(window, Qt.Key.Key_Escape)
        settle(app, 200)
        assert not dialog.property("visible")
    assert backend.destructive_calls == []
    checks.append("clear/disconnect dialogs fit narrow window, default to cancel and Escape never mutates")

    assert not warnings, warnings
    result = {"passed": True, "platform": "offscreen", "network": "none", "checks": checks, "qmlWarnings": warnings}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    window.hide()
    window.deleteLater()
    settle(app, 20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
