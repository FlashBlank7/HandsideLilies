from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_connector_failures_and_slack_inbox_anchor_are_real_actions() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_entry_actions.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=40,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["passed"] is True
    assert result["connectorObjectFailure"]["notice"] == "缺少 Client ID"
    assert result["connectorBooleanFailure"]["oauthCalls"] == 1
    race = result["slackInboxDisconnectRace"]
    assert race["passed"] is True
    assert race["presentationCountAfterDisconnect"] == 0
    assert race["lastAnchorAfterDisconnect"] == ""
    assert race["pendingAnchorAfterDisconnect"] == ""
    assert race["presentationCountAfterRejectedRequest"] == 0
    assert race["pendingAnchorAfterRejectedRequest"] == ""
    assert race["presentationCountAfterReconnect"] == 0
    assert result["slackInboxAnchor"]["backendCalls"] == 1
    assert result["slackInboxAnchor"]["lastAnchor"] == "slack-inbox"
    assert result["slackInboxAnchor"]["presentationCount"] == 1
    assert result["slackInboxAnchor"]["activeFocus"] is True
    for anchor, section in (
        ("focus", "work"),
        ("reading", "work"),
        ("wardrobe", "growth"),
    ):
        route = result["workPanelAnchors"][anchor]
        assert route["passed"] is True
        assert route["section"] == section
        assert route["lastAnchor"] == anchor
        assert route["highlightedAnchor"] == anchor
        assert route["targetVisible"] is True
        assert route["targetFocused"] is True

    main_source = (PROJECT_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
    assert 'action === "focus"' in main_source
    assert 'desktop.openWorkPanel("focus")' in main_source
    assert 'action === "reading"' in main_source
    assert 'desktop.openWorkPanel("reading")' in main_source
