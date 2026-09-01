from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_drag_runtime_regressions_v0325_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_drag_runtime_v0325.py"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    outcome = json.loads(completed.stdout)
    assert all(
        outcome[name]["passed"] is True
        for name in (
            "windowGlideFreezesSynchronouslyOnPress",
            "directThresholdAndBridge",
            "directNearEdgeRelease",
            "characterGrabContinuity",
            "localPoseMotionDoesNotBecomeDrag",
            "singlePersistenceWithoutSettleMovement",
            "interruptedDragCommitsBeforeHide",
            "staleScreenConstraintIgnored",
            "nativeStartCancelReentry",
            "expandedMenuNativePressStopsMotion",
            "nativeOutAndBackLatch",
            "failedNativeStartFallsBackDirect",
            "directReleaseHideRaceFinalizesExactlyOnce",
            "avoidanceInteractionGuard",
        )
    )
