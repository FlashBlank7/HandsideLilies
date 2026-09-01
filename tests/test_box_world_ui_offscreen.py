from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_box_world_progress_and_actions_render_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["QT_QPA_OFFSCREEN_SIZE"] = "1200x900"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_box_world_ui.py")],
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
    report = json.loads(completed.stdout)
    assert report["sceneOpen"] is True
    assert report["requestedVisible"] is True
    assert report["visible"] is True
    assert report["windowTitle"] == "莉莉丝 · 盒中世界"
    assert report["renderedObjectRows"] == 6
    assert report["renderedPlacedDecorations"] >= 1
    assert all(
        report[name]
        for name in (
            "stageFound",
            "characterFound",
            "progressFound",
            "manageActionFound",
            "exitActionFound",
            "screenshotSaved",
        )
    )
