from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_baked_pose_artwork_falls_back_to_visible_outfit_layers() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_pose_outfit_policy_ui.py")],
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
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["platform"] == "offscreen"
    assert report["passed"] is True
    # All six outfits are independent. Existing baked activity artwork depicts
    # first-encounter clothes only; every other outfit keeps its own layers.
    assert report["bakedCount"] == 5
    assert report["layeredFallbackCount"] >= 18
    focus_cases = [case for case in report["cases"] if case["pose"] == "focus-watch"]
    assert {case["outfit"] for case in focus_cases if case["usesPoseArtwork"]} == {
        "first-encounter",
    }
    assert all(case["passed"] for case in report["cases"])
    summer_cases = [case for case in report["cases"] if case["outfit"] == "summer-cotton-dress"]
    assert {case["pose"] for case in summer_cases} == {"focus-watch", "reading", "presenting", "box-support", "resting"}
    assert all(not case["usesPoseArtwork"] for case in summer_cases)
    assert all(
        case["usesPoseArtwork"] is False
        for case in report["cases"]
        if case["expectedRenderer"] == "layered-outfit"
    )
