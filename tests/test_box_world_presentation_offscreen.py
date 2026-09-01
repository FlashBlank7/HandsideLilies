from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOX_WORLD_PROCESS_TIMEOUT_SECONDS = 30


def test_compact_pet_world_action_presents_independent_scene_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_box_world_presentation.py"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # Cold Qt Quick plugin discovery can consume about ten seconds on a
        # clean Windows cache before the scripted interactions even begin.
        # Keep the product assertions strict, but leave enough process-level
        # headroom that a healthy cold start is not killed by the harness.
        timeout=BOX_WORLD_PROCESS_TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(completed.stdout)
    first = report["firstPresentation"]
    assert first["entered"] is True
    assert first["backendOpen"] is True
    assert first["workPanelClosed"] is True
    assert first["sceneVisible"] is True
    assert first["sceneExposed"] is True
    assert first["sceneTitle"] == "莉莉丝 · 盒中世界"
    assert first["stageFound"] is True
    assert first["manageActionFound"] is True
    assert first["exitActionFound"] is True
    assert first["presentationCount"] >= 1

    repeated = report["repeatAfterMinimize"]
    assert repeated["minimizedBefore"] is True
    assert repeated["restoredFromMinimized"] is True
    assert repeated["presentationCountAdvanced"] is True
    assert repeated["restoredIntoPetWorkArea"] is True
    assert repeated["sceneVisible"] is True

    assert report["closed"] == {
        "exitActionFound": True,
        "explicitCloseClicked": True,
        "sceneHidden": True,
        "backendClosed": True,
        "workPanelStillClosed": True,
    }


def test_box_world_scene_remains_reachable_at_two_hundred_percent_scale() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_QPA_OFFSCREEN_SIZE"] = "1200x900"
    environment["QT_SCALE_FACTOR"] = "2"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_box_world_presentation.py"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=BOX_WORLD_PROCESS_TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(completed.stdout)
    presentation = report["firstPresentation"]
    assert presentation["sceneFitsLogicalScreen"] is True
    assert presentation["narrowViewport"] is True
    assert presentation["sceneVisible"] is True
    assert presentation["sceneTitle"] == "莉莉丝 · 盒中世界"
