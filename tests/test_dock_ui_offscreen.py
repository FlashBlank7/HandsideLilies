from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_grouped_paper_dock_stresses_1_4_12_50_windows_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_dock_ui.py")],
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
    report = json.loads(completed.stdout)
    scenarios = report["scenarios"]
    assert set(scenarios) == {"1-window", "4-windows", "12-windows", "50-windows"}
    assert all(scenario["passed"] for scenario in scenarios.values())
    assert scenarios["12-windows"]["dockButtonCount"] == 7
    assert scenarios["12-windows"]["overflowGroupCount"] == 1
    assert scenarios["50-windows"]["drawer"]["rowCount"] == 61
    assert (
        scenarios["50-windows"]["drawer"]["instantiatedRows"]
        < scenarios["50-windows"]["drawer"]["rowCount"]
    )
    assert scenarios["50-windows"]["preview"]["countLabel"] == "6 / 8"

    assert report["staleActivation"] == {
        "attemptedHandle": True,
        "drawerPreserved": True,
        "passed": True,
    }

    launch = report["launchScenario"]
    assert launch["passed"], launch
    assert launch["mergedDockCount"] == 5
    assert launch["drawerGroupCount"] == 6
    assert launch["searchLaunchCount"] == 1
    assert launch["opened"] == [
        "paper-library",
        "research-report",
        "research-report",
    ]
    assert launch["mergedNames"].count("Word") == 1
    assert launch["mergedNames"].count("WordPad") == 1
    assert launch["mergedNames"].count("WPS Office") == 1
    assert launch["mergedNames"].count("Visual Studio Code") == 1
    assert launch["collapsedGeometry"]["width"] == 64
    assert launch["collapsedGeometry"]["height"] == 16
    assert launch["collapsedGeometry"]["bottomGap"] == 0
    assert launch["collapsedGeometry"]["paperWidth"] == 64
    assert launch["collapsedGeometry"]["paperHeight"] == 6
    assert all(launch["checks"].values()), launch["checks"]
