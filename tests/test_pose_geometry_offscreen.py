from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("qt_scale_factor", ("1.25", "1.5", "1.75", "2"))
def test_sprite_pose_geometry_at_compact_extremes_and_high_dpi(
    qt_scale_factor: str,
) -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["QT_SCALE_FACTOR"] = qt_scale_factor
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_pose_geometry_ui.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["platform"] == "offscreen"
    assert report["qtScaleFactor"] == qt_scale_factor
    assert report["devicePixelRatio"] >= float(qt_scale_factor) - 0.01
    assert report["passed"] is True
    assert [entry["name"] for entry in report["viewports"]] == [
        "extreme-narrow",
        "extreme-short",
        "tiny",
        "emergency-compact",
        "standard-compact",
        "large-compact",
    ]
    for viewport in report["viewports"]:
        assert viewport["passed"] is True
        assert set(viewport["poses"]) == {
            "reading", "presenting", "box-support", "resting",
        }
        for pose in viewport["poses"].values():
            assert pose["passed"] is True
            assert pose["frameInside"] is True
            assert pose["exactAspect"] is True
            assert pose["hitInsideFrame"] is True
            assert pose["maskCenterHit"] is True
            assert pose["transparentCornerHit"] is False
            assert pose["outsideHit"] is False
            assert pose["cordInsideMask"] is True
            assert pose["cordAnchorError"] <= 0.05
    for transition in report["transitions"]:
        assert transition["passed"] is True
        assert transition["instantCordJump"] <= 1.5
        assert transition["maxCordStep"] <= 60.0
        assert transition["finite"] is True
        assert transition["frameStayedInside"] is True
