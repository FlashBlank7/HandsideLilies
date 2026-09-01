from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_click_masks_drive_real_qml_hit_testing_and_mirroring(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    report_path = tmp_path / "pose-click-mask-v0345.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_pose_click_masks.py"),
            "--executable",
            sys.executable,
            "--report-path",
            str(report_path),
            "--resource-root",
            str(PROJECT_ROOT),
        ],
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
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schemaVersion"] == 1
    assert report["applicationVersion"] == "0.3.45"
    assert report["executableSha256"] == hashlib.sha256(
        Path(sys.executable).read_bytes()
    ).hexdigest().upper()
    assert Path(report["resourceRoot"]) == PROJECT_ROOT.resolve()
    assert datetime.fromisoformat(report["capturedAt"]).tzinfo is not None
    assert report["passed"] is True
    assert report["platform"] == "offscreen"
    assert set(report["poses"]) == {
        "perch-prone",
        "title-sit",
        "edge-peek-live",
        "listening-live",
        "focus-watch",
    }
    for pose in report["poses"].values():
        assert pose["passed"] is True
        assert pose["declaredHit"] is True
        assert pose["transparentCornerHits"] == [False, False, False, False]
    assert report["mirror"] == {
        "displayedMirror": True,
        "unmirroredLeft": False,
        "unmirroredRight": True,
        "mirroredLeft": True,
        "mirroredRight": False,
        "passed": True,
    }
    transition = report["transition"]
    assert transition["gridSize"] == [19, 19]
    assert transition["unreadyTargetNeverCommitted"] is True
    assert transition["progressZeroUsesOldOnly"] is True
    assert transition["midpointUsesVisibleUnion"] is True
    assert transition["progressOneUsesNewOnly"] is True
    assert transition["oldOnlyCellCount"] > 0
    assert transition["newOnlyCellCount"] > 0
    assert report["loadFailure"]["targetLoadFailed"] is True
    assert set(report["loadFailure"]["outfits"]) == {
        "first-encounter",
        "summer-cotton-dress",
        "home-cardigan",
        "reading-smock",
        "focus-coat",
        "rest-nightdress",
    }
    assert all(
        outfit["passed"]
        and outfit["cordFallbackDrift"] <= 0.01
        for outfit in report["loadFailure"]["outfits"].values()
    )
    assert report["rapidSwitch"]["passed"] is True
    assert report["rapidSwitch"]["finalAssetKey"] == "poseTitleSit"


def test_pose_click_mask_verifier_fails_closed_for_missing_resources(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "must-not-exist.json"
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_pose_click_masks.py"),
            "--executable",
            sys.executable,
            "--report-path",
            str(report_path),
            "--resource-root",
            str(tmp_path / "missing-internal"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode != 0
    assert not report_path.exists()
