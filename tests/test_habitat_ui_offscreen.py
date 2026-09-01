from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_adaptive_habitat_transitions_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_habitat_ui.py")],
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
    report_path = PROJECT_ROOT / "artifacts" / "habitat-pose-coverage.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["platform"] == "offscreen"
    assert report["assetGate"]["passed"] is True
    assert report["assetGate"]["errors"] == []
    coverage = report["poseCoverage"]
    assert coverage["passed"] is True
    assert coverage["windowClasses"] == 9
    assert coverage["uniqueArtwork"] == [
        "poseListeningLive",
        "poseTitleSit",
    ]
    assert coverage["uniqueArtworkCount"] == 2
    assert coverage["proceduralLayeredProfiles"] == [
        "large-perch",
        "maximized-edge",
        "medium-perch",
        "micro-window-edge",
        "narrow-caption-edge",
        "offscreen-window-edge",
    ]
    assert coverage["microUsesVerifiedFallback"] is False
    assert coverage["microUsesProceduralLayeredFallback"] is True
    assert coverage["mediumUsesProceduralLayeredFallback"] is True
    assert coverage["uniqueRuntimeVariantCount"] == 9
    assert coverage["uniqueVisualSignatureCount"] == 9
    assert coverage["visualSignaturesDistinct"] is True
    assert coverage["constrainedProfilesDistinct"] is True
    assert coverage["contactStableDuringMotion"] is True
    assert coverage["wholeArtworkScaleDisabled"] is True
    assert coverage["transformedFramesInsidePetWindow"] is True
    assert coverage["runtimeVariants"] == {
        "large-perch": "wide-window-sprawl",
        "maximized-edge": "screen-edge-watch",
        "medium-perch": "window-perch",
        "micro-window-edge": "micro-corner-grip",
        "narrow-caption-edge": "caption-side-lean",
        "offscreen-window-edge": "cautious-return",
        "portrait-title": "portrait-title-watch",
        "small-title": "title-sit-balance",
        "top-space-listen": "edge-listen",
    }
    assert coverage["motionStyles"] == {
        "large-perch": "perch-stretch",
        "maximized-edge": "screen-watch",
        "medium-perch": "perch-breathe",
        "micro-window-edge": "corner-grip",
        "narrow-caption-edge": "caption-lean",
        "offscreen-window-edge": "cautious-peek",
        "portrait-title": "portrait-listen",
        "small-title": "title-balance",
        "top-space-listen": "edge-listen",
    }
    for profile in report["profiles"].values():
        # A profile change is not finished merely because its artwork source
        # has cross-faded.  Scale, contact, anchor and silhouette-height
        # behaviours must remain part of the same public transition contract.
        assert profile["transitionStarted"] is True
        assert profile["settled"] is True
        assert max(abs(value) for value in profile["finalAnchorError"]) <= 0.05
        assert profile["motionChanged"] is True
        assert profile["maxTransformedAnchorError"] <= 0.05
        assert profile["maxTransformedFrameOverflow"] <= 0.05
        assert all(
            sample["scaleX"] == 1.0 and sample["scaleY"] == 1.0
            for sample in profile["motionSamples"]
        )
        assert profile["maxCordEndpointDrift"] <= 0.05
        assert profile["maxPublicBoundsDrift"] <= 0.05
    assert coverage["cordStableDuringMotion"] is True
    assert coverage["publicBoundsStableDuringMotion"] is True
    sprite_poses = report["spritePoses"]
    assert set(sprite_poses) == {"reading", "presenting", "box-support", "resting"}
    for pose in sprite_poses.values():
        assert pose["passed"] is True
        assert pose["transitionStarted"] is True
        assert pose["instantCordJump"] <= 1.5
        assert pose["cordInsideFrame"] is True
    assert all(
        sprite_poses[pose_id]["outgoingVisibleMidTransition"] is True
        for pose_id in ("presenting", "box-support", "resting")
    )
