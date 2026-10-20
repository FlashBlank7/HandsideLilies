"""Click first-time Slack setup offscreen; no accounts, sockets or credentials."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
os.environ["QML_DISABLE_DISK_CACHE"] = "1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import Property, QUrl, Slot
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication

from lilies.backend import Backend
from verify_connector_usability import UsabilityBackend
from verify_connector_policy_hydration import reveal_and_click, settle, text, visual


class BootstrapBackend(UsabilityBackend):
    def __init__(self):
        super().__init__()
        self._slack_status.update(configuration={}, socketReady=False)
        self._slack_status["policyCanonical"] = {
            "scope": "necessary", "interruption": "quiet", "retention": "metadata",
            "assistance": "assist", "selectedSources": [],
        }
        self.preview_calls = []
        self.saved_manifest_reads = 0
        self.fail_preview = False

    @Property(str, notify=UsabilityBackend.connectorStatusChanged)
    def slackManifestText(self):
        self.saved_manifest_reads += 1
        return "saved-manifest-must-not-be-used"

    @Slot("QVariantMap", result="QVariantMap")
    def slackGenerateManifest(self, draft):
        self.preview_calls.append(copy.deepcopy(draft))
        if self.fail_preview:
            return {"ok": False, "error": "示例失败：不使用旧配置"}
        # Exercise the production entry point and builder, not a canned JSON.
        return Backend.slackGenerateManifest(self, draft)


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "artifacts" / "slack-bootstrap"
    if output.drive.casefold() != "f:":
        raise ValueError("UI artifacts must stay on F drive")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    for name in ("msyh.ttc", "segoeui.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei", 10))
    assert QFontMetrics(app.font()).inFontUcs4(ord("莉"))
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
    backend = BootstrapBackend()
    saved_status = copy.deepcopy(backend._slack_status)
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "V03ConnectorSetup.qml")))
    while component.isLoading():
        settle(app, 10)
    assert not component.isError(), [e.toString() for e in component.errors()]
    window = component.createWithInitialProperties({"appBackend": backend, "provider": "slack", "section": "settings"})
    assert isinstance(window, QQuickWindow)
    window.resize(480, 760)
    window.show()
    settle(app, 160)
    scroll = visual(window, "connectorSetupScroll")

    def click(name):
        reveal_and_click(window, visual(window, name), scroll, app)

    def generate():
        click("connectorGenerateSlackManifest")
        return text(visual(window, "connectorSlackManifestPreview"))

    def capture(name):
        settle(app, 80)
        assert window.grabWindow().save(str(output / (name + ".png")))

    checks = []
    assert text(visual(window, "connectorSlackClientId")) == ""
    assert text(visual(window, "connectorSlackXappToken")) == ""
    assert not backend.preview_calls and not backend.configure_calls and not backend.oauth_calls
    manifest = json.loads(generate())
    assert manifest["oauth_config"]["scopes"]["user"] == ["im:history", "channels:history"]
    assert manifest["settings"]["event_subscriptions"]["user_events"] == ["message.channels", "message.im"]
    assert backend._slack_status == saved_status
    assert visual(window, "connectorCopySlackManifest").isEnabled()
    capture("first-time-480")
    checks.append("blank first-time setup uses production preview without save, authorization or account")

    for control, value in (("connectorSlackXappToken", "xapp-synthetic-secret"),
                           ("connectorSlackClientId", "unsaved-client"),
                           ("connectorSlackCurrentUserId", "U-NOT-IN-MANIFEST"),
                           ("connectorSlackChannels", "C-NOT-IN-MANIFEST")):
        visual(window, control).setProperty("text", value)
    assert json.loads(generate()) == manifest
    request = backend.preview_calls[-1]
    assert set(request) == {"policy", "redirectUri"}
    assert set(request["policy"]) == {"scope", "assistance"}
    assert "xapp-" not in json.dumps(request)
    assert text(visual(window, "connectorSlackXappToken")) == "xapp-synthetic-secret"
    checks.append("preview payload excludes token, IDs and local channel whitelist; unsaved draft is not submitted")

    visual(window, "connectorPolicyScope").setProperty("currentIndex", 2)
    visual(window, "connectorPolicyAssistance").setProperty("currentIndex", 2)
    settle(app)
    assert text(visual(window, "connectorSlackManifestPreview")) == ""
    assert not visual(window, "connectorCopySlackManifest").isEnabled()
    broad = json.loads(generate())
    assert "chat:write" in broad["oauth_config"]["scopes"]["user"]
    assert len(broad["settings"]["event_subscriptions"]["user_events"]) == 4
    assert backend._slack_status == saved_status, "preview must not persist wider authority"
    checks.append("scope/assistance edits invalidate stale preview; broader draft does not broaden saved policy")

    visual(window, "connectorSlackRedirectUri").setProperty("text", "custom://not-supported")
    settle(app)
    assert text(visual(window, "connectorSlackManifestPreview")) == ""
    assert generate() == ""
    assert "本机回调" in window.property("notice")
    visual(window, "connectorSlackRedirectUri").setProperty("text", "http://127.0.0.1:53683/another-callback")
    assert json.loads(generate())["oauth_config"]["redirect_urls"] == ["http://127.0.0.1:53683/another-callback"]
    checks.append("unsupported redirect fails closed and corrected local address uses current draft")

    backend.connectorStatusChanged.emit()
    settle(app)
    assert "chat:write" in json.loads(text(visual(window, "connectorSlackManifestPreview")))["oauth_config"]["scopes"]["user"]
    backend.fail_preview = True
    assert generate() == ""
    assert "示例失败" in window.property("notice")
    assert backend.saved_manifest_reads == 0
    backend.fail_preview = False
    assert generate()
    window.setProperty("provider", "calendar")
    settle(app)
    assert text(visual(window, "connectorSlackManifestPreview")) == ""
    assert text(visual(window, "connectorSlackXappToken")) == ""
    window.setProperty("provider", "slack")
    assert generate()
    checks.append("background refresh retains draft; errors and provider switches never reuse saved/stale manifest")

    # Do not expose even synthetic token strings in screenshots. The actual
    # copy action is intentionally not triggered (no host clipboard writes).
    visual(window, "connectorSlackClientId").setProperty("text", "")
    visual(window, "connectorSlackCurrentUserId").setProperty("text", "")
    visual(window, "connectorSlackChannels").setProperty("text", "")
    for width in (480, 680):
        window.resize(width, 760)
        settle(app)
        click("connectorGenerateSlackManifest")
        preview_scroll = visual(window, "connectorSlackManifestScroll")
        assert preview_scroll.width() <= window.width() - 24
        assert preview_scroll.height() == 190
        capture(f"preview-{width}")
    assert not backend.configure_calls and not backend.oauth_calls
    assert not backend.assist_calls and not backend.confirm_calls and not backend.open_calls
    assert not backend.destructive_calls
    assert backend._slack_status == saved_status
    assert not warnings, warnings
    checks.append("480/680px preview stays bounded; no account, browser, model, credential or external write")
    result = {"passed": True, "platform": "offscreen", "network": "none", "checks": checks, "qmlWarnings": warnings}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    window.hide()
    window.deleteLater()
    settle(app, 20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
