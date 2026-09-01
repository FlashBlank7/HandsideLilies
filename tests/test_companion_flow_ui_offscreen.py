from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_default_companion_flow_reaches_a_visible_offscreen_bubble() -> None:
    """Exercise the shipped QML with synthetic app/idle signals only."""

    environment = dict(os.environ)
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QSG_RHI_BACKEND": "software",
            "PYTHONUTF8": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_companion_flow_ui.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, (
        f"offscreen companion flow failed\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    report = json.loads(completed.stdout)
    assert report["passed"] is True
    assert report["timeline"]["bubbleWindowVisible"] is True
    assert report["timeline"]["bubbleModel"] == "synthetic-subscription"
    assert report["timeline"]["bubbleDurationSeconds"] == 240
    assert report["timeline"]["schedulerStartedAtEnable"] is True
    assert report["timeline"]["schedulerIntervalMs"] == 1500
    assert report["timeline"]["schedulerActiveBeforeEmission"] is True
    assert report["timeline"]["staysOnTop"] is True
    assert report["timeline"]["doesNotAcceptFocus"] is True
    assert (
        report["timeline"]["replacementPresentationRevision"]
        > report["timeline"]["firstPresentationRevision"]
    )
    assert report["entries"]["settingsLibrary"]["pageAfterOpen"] == 3
    assert report["entries"]["settingsLibrary"]["openInsideWindow"] is True
    assert report["defaults"]["captureStagingCreated"] is False
    policy_ui = report["applicationPoliciesUi"]
    assert policy_ui["emptyInitially"] is True
    assert policy_ui["identity"] == "wps.exe"
    assert policy_ui["initialPolicy"] == "静默"
    assert policy_ui["allowApplied"] is True
    assert policy_ui["policyAfterAllow"] == "允许气泡"
    assert policy_ui["resetApplied"] is True
    assert policy_ui["sensitivePolicy"] == "静默"
    assert policy_ui["sensitiveAllowEnabled"] is False
    assert policy_ui["leakedTitleOrContent"] is False
