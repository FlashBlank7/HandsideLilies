from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_procedural_habitat_poses_are_distinct_and_work_with_every_outfit() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_procedural_habitat_poses.py"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(
        (PROJECT_ROOT / "artifacts" / "procedural-habitat-pose-gate.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["passed"] is True
    assert report["variantCount"] == 9
    assert report["outfitCount"] == 6
    assert report["uniqueGeometrySignatureCount"] == 9
    assert report["uniqueRegionalSignatureCountWithoutBodyRotation"] == 9
    assert report["uniquePixelSignatureCountWithoutBodyRotation"] == 9
    assert report["optionalAssetUrlsEmpty"] == {
        "poseEdgeLeanV1": True,
        "poseMicroCornerGripV1": True,
        "poseWideWindowSprawlV1": True,
        "poseWindowDangleV1": True,
        "poseWindowProneV2": True,
    }
    for variant_id, variant in report["variants"].items():
        assert variant["passed"] is True
        assert len(variant["outfits"]) == 6
        for outfit in variant["outfits"].values():
            assert outfit["passed"] is True
            assert outfit["usesPoseArtwork"] is False
            assert outfit["poseArtworkKey"] == ""
            assert outfit["anchorError"] <= 0.05
            if variant_id.startswith("window-perch"):
                assert outfit["renderedContactY"] <= 0.50
                assert outfit["renderedContactY"] != outfit["backendContactY"]
                assert outfit["contactInShoulderRegion"] is True
                assert outfit["belowContactExtent"] >= 60.0
            assert outfit["boundsInside"] is True
            assert outfit["hitAreaEnabled"] is True
            assert outfit["hitValid"] is True
