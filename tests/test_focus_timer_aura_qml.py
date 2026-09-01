from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_focus_timer_aura_offscreen_states_and_window_contract() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_focus_timer_aura.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])
    assert outcome["passed"] is True
    assert outcome["offscreenPlatform"] == "offscreen"
    assert Path(outcome["report"]).is_file()
    assert outcome["idle"] == {
        "shouldShow": False,
        "visible": False,
        "targetFps": 0,
        "motionStopped": True,
    }

    running = outcome["running"]
    assert outcome["minuteBoundary"] == {
        "before": {
            "state": "running",
            "time": "04:01",
            "used": "已用 00:59 / 05:00",
            "remainingSeconds": 241,
            "progressTarget": 59 / 300,
        },
        "paused": {
            "state": "paused",
            "time": "04:00",
            "used": "已用 01:00 / 05:00",
            "remainingSeconds": 240,
            "progressTarget": 60 / 300,
            "motionStopped": True,
        },
        "resumed": {
            "state": "running",
            "time": "03:59",
            "used": "已用 01:01 / 05:00",
            "remainingSeconds": 239,
            "progressTarget": 61 / 300,
        },
    }
    assert running["state"] == "running"
    assert running["label"] == "专注中"
    assert running["remainingLabel"] == "专注中 · 剩余"
    assert running["time"] == "03:59"
    assert running["elapsedText"] == "01:01"
    assert running["durationText"] == "05:00"
    assert running["usedTimeText"] == "已用 01:01 / 05:00"
    assert running["visibleRemainingLabel"] == running["remainingLabel"]
    assert running["visibleUsedTimeText"] == running["usedTimeText"]
    assert running["elapsedSeconds"] == 61
    assert running["remainingSeconds"] == 239
    assert running["breathing"] is True
    assert running["breathMoved"] is True
    assert running["orbitMoved"] is True
    assert running["progressHeadMoved"] is True
    assert running["shouldShow"] is True
    assert abs(running["progressTarget"] - 61 / 300) < 1e-6
    assert 0.0 < running["animatedProgress"] <= 61 / 300

    assert outcome["lowPower"]["targetFps"] == 15
    assert outcome["lowPower"]["stillAnimated"] is True
    assert outcome["lowPower"]["reducedCadence"] is True
    assert outcome["lowPower"]["fullMotionTicks"] > (
        outcome["lowPower"]["lowMotionTicks"] * 2
    )
    assert outcome["hiddenActive"] == {
        "visible": False,
        "canAnimate": False,
        "targetFps": 0,
        "motionStopped": True,
        "startPulseCleared": True,
    }
    assert set(outcome["textScaling"]) == {"compact", "standard", "large"}
    for label, expected_extent in (("compact", 144), ("standard", 176), ("large", 208)):
        value = outcome["textScaling"][label]
        assert value["extent"] == [expected_extent, expected_extent]
        assert value["remaining"] == "专注中 · 剩余"
        assert value["used"] == "已用 01:01 / 05:00"
        assert value["stateFits"] is True
        assert value["usedFits"] is True

    assert abs(
        outcome["progressEndpoint"]["angle"]
        - outcome["progressEndpoint"]["expectedAngle"]
    ) < 0.1
    assert outcome["runningScreenshot"]["saved"] is True
    assert outcome["runningScreenshot"]["size"] == [176, 176]
    assert Path(outcome["runningScreenshot"]["path"]).is_file()

    assert outcome["paused"] == {
        "state": "paused",
        "label": "已暂停",
        "remainingLabel": "已暂停 · 剩余",
        "time": "03:55",
        "usedTimeText": "已用 01:05 / 05:00",
        "breathing": False,
        "orbitStopped": True,
        "progressHeadStopped": True,
        "shouldShow": True,
    }
    assert outcome["resumed"] == {
        "state": "running",
        "remainingLabel": "专注中 · 剩余",
        "time": "03:54",
        "usedTimeText": "已用 01:06 / 05:00",
        "orbitMoved": True,
        "progressHeadAdvanced": True,
    }
    assert outcome["nearDeadline"]["time"] == "00:01"
    assert outcome["nearDeadline"]["remainingSeconds"] == 1
    assert abs(outcome["nearDeadline"]["progressTarget"] - 299 / 300) < 1e-6
    assert abs(outcome["nearDeadline"]["progressHeadAngle"] - 299 / 300 * 360) < 0.1
    assert outcome["completed"] == {
        "state": "completed",
        "label": "专注完成",
        "time": "00:00",
        "usedTimeText": "已用 05:00 / 05:00",
        "completionVisible": True,
        "shouldShow": True,
    }
    assert outcome["newSessionStart"]["state"] == "running"
    assert outcome["newSessionStart"]["time"] == "05:00"
    assert outcome["newSessionStart"]["progressTarget"] == 0.0
    assert outcome["newSessionStart"]["animatedProgress"] == 0.0
    assert outcome["newSessionStart"]["startPulseActive"] is True
    assert outcome["quickCancel"] == {
        "state": "cancelled",
        "startPulseCleared": True,
        "completionPulseCleared": True,
    }
    assert outcome["finished"] == {
        "state": "finished",
        "label": "已结束",
        "time": "03:55",
        "usedTimeText": "已用 01:05 / 05:00",
        "completionVisible": True,
        "progress": 65 / 300,
    }
    assert outcome["cancelled"] == {
        "state": "cancelled",
        "label": "已取消",
        "time": "04:20",
        "usedTimeText": "已用 00:40 / 05:00",
        "completionVisible": True,
        "progress": 40 / 300,
    }
    assert outcome["afterCancelStart"] == {
        "state": "running",
        "completionVisible": False,
        "completionPulseCleared": True,
        "progressReset": True,
    }
    assert outcome["placement"] == {
        "usesSide": True,
        "sideGapHeld": True,
        "verticalCenterHeld": True,
    }
    assert set(outcome["scaling"]) == {
        "standard",
        "negativePortrait",
        "highDpiEmergency",
        "microEmergency",
    }
    for value in outcome["scaling"].values():
        assert abs(value["extent"][0] - value["expectedExtent"]) <= 1
        assert abs(value["extent"][1] - value["expectedExtent"]) <= 1
        assert value["insideWorkArea"] is True
        assert value["surfaceInsideWindow"] is True
        assert value["scaledRingReadable"] is True
        assert 0 < value["dialScale"] <= 1
        assert value["ringInset"] >= 2
        assert value["ringStroke"] >= 1.5
    assert outcome["silent"] == {
        "state": "silent",
        "suppressed": True,
        "shouldShow": False,
        "visible": False,
    }
    assert outcome["window"] == {
        "visibleDuringVerification": False,
        "doesNotAcceptFocus": True,
        "transparentForInput": True,
        "independent": True,
        "ringExists": True,
        "timeLabelExists": True,
        "usedTimeLabelExists": True,
        "orbitKnotExists": True,
        "activitySweepExists": True,
        "startWaveExists": True,
    }
