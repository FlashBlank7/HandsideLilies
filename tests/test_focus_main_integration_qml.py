from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_real_backend_focus_drives_main_aura_and_ephemeral_pose() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_focus_main_integration.py"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])
    assert outcome["passed"] is True
    assert outcome["explicitStart"] == {
        "buttonVisible": True,
        "activeFromClick": True,
        "auraVisible": True,
        "startPulseActive": True,
        "temporaryFps": 60,
        "motionTicksAdvanced": True,
        "orbitAdvanced": True,
    }
    assert outcome["lowPowerHandoff"] == {
        "lowPower": True,
        "startPulseStillActive": True,
    }
    assert outcome["running"] == {
        "pose": "focus-watch",
        "state": "running",
        "time": "05:00",
        "remainingLabel": "专注中 · 剩余",
        "usedTimeText": "已用 00:00 / 05:00",
        "targetFps": 15,
        "startPulseCleared": True,
        "visible": True,
    }
    assert outcome["small"]["extent"] == [144, 144]
    assert outcome["progressed"] == {
        "time": "04:59",
        "usedTimeText": "已用 00:01 / 05:00",
        "progressTarget": 1 / 300,
    }
    assert outcome["large"]["extent"] == [208, 208]
    assert outcome["large"]["figureHeight"] > outcome["small"]["figureHeight"] * 2
    assert outcome["chatPose"] == "listening-live"
    assert outcome["postChatPose"] == "focus-watch"
    assert outcome["paused"]["pose"] != "focus-watch"
    assert outcome["paused"]["remainingLabel"] == "已暂停 · 剩余"
    assert outcome["paused"]["targetFps"] == 0
    assert outcome["pausedSilentPresence"] == (
        "当前 · 全屏界面中保持静默；离开全屏后莉莉丝会自动回来，专注仍保持暂停"
    )
    assert outcome["pausedSilentContinuity"] == {
        "auraVisible": False,
        "elapsedAfter": 1,
        "elapsedBefore": 1,
        "presenceText": outcome["pausedSilentPresence"],
        "sessionSame": True,
    }
    assert outcome["resumedPose"] == "focus-watch"
    assert outcome["silentRunning"]["visible"] is False
    assert outcome["silentRunning"]["presenceText"] == (
        "当前 · 全屏界面中保持静默；离开全屏后莉莉丝会自动回来，专注计时仍在后台继续"
    )
    assert outcome["silentRunning"]["rootPresenceText"] == outcome["silentRunning"]["presenceText"]
    assert outcome["silentRunning"]["sessionSame"] is True
    assert outcome["silentRunning"]["elapsedBefore"] == 1
    assert outcome["silentRunning"]["elapsedAfter"] == 3
    assert outcome["restoredRunning"]["visible"] is True
    assert outcome["restoredRunning"]["presenceText"] == "当前 · 莉莉丝正在桌面安静停驻"
    assert outcome["restoredRunning"]["time"] == "04:57"
    assert outcome["restoredRunning"]["usedTimeText"] == "已用 00:03 / 05:00"
    assert outcome["blockedRunning"] == {
        "presenceText": "当前 · 受保护或敏感界面中暂时隐藏；离开后莉莉丝会自动回来，专注计时仍在后台继续",
        "suppressed": True,
        "visible": False,
    }
    assert outcome["hiddenCompletion"]["completionVisible"] is True
    assert outcome["hiddenCompletion"]["presenceText"] == (
        "当前 · 全屏界面中保持静默；离开全屏后莉莉丝会自动回来"
    )
    assert outcome["restoredCompletion"]["visible"] is True
    assert outcome["loadoutUnchanged"] is True
    assert outcome["focusQmlWarningCount"] == 0

    main_source = (PROJECT_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
    assert "lowPower: !backend.sceneActive || !compactWindow.highMotion" in main_source
    work_panel_source = (PROJECT_ROOT / "qml" / "V03WorkPanel.qml").read_text(
        encoding="utf-8"
    )
    assert 'objectName: "focusStartButton"' in work_panel_source
    assert 'objectName: "petPresenceStatusLabel"' in main_source
    assert "visible: !diagnosticWindowProbe && !desktop.petPresenceSuppressed" in main_source
