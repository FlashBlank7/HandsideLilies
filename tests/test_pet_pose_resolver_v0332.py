from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_contextual_pose_resolver_has_fixed_priorities_and_safe_habitat_fallback() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_pet_pose_resolver.py")],
        cwd=ROOT,
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
    assert outcome["platform"] == "offscreen"
    assert outcome["default"] == {
        "pose": "resting", "context": "equipped", "highMotion": False
    }
    assert outcome["focus"]["pose"] == "focus-watch"
    assert outcome["reading"]["pose"] == "reading"
    assert outcome["companion"] == {
        "pose": "presenting", "context": "companion", "highMotion": False
    }
    assert outcome["selection"] == {
        "pose": "reading", "context": "selection", "highMotion": False
    }
    assert outcome["chat"] == {
        "pose": "listening-live", "context": "chat", "highMotion": False
    }
    assert outcome["habitat"]["topSpaceListen"]["pose"] == "listening-live"
    assert outcome["habitat"]["unknown"]["pose"] == "resting"
    assert outcome["pausedFocus"] == outcome["default"]


def test_main_uses_the_resolver_for_pose_and_animation_budget() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    assert "V03PetPoseResolver {" in source
    assert "pose: petPoseResolver.resolvedPose" in source
    assert "|| petPoseResolver.requiresHighMotion" in source
    assert "companionBubble: backend.companionService.bubble || ({})" in source
    assert "readingStatus: backend.readingStatus || ({})" in source
