from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("qt_scale_factor", ("1", "1.5", "2"))
def test_cross_dpi_and_extreme_work_area_layouts_offscreen(
    qt_scale_factor: str,
) -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    environment["QT_SCALE_FACTOR"] = qt_scale_factor
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_cross_dpi_layout.py")],
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
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])
    assert outcome["platform"] == "offscreen"
    assert outcome["nativeThresholdEventTime"]["passed"] is True
    assert outcome["nativeThresholdEventTime"]["qtScaleFactor"] == qt_scale_factor
    assert outcome["dragTimerCapturedEvent"]["dragMode"] == "direct"
    assert outcome["passed"] is True
    assert outcome["focusAuraBinding"]["passed"] is True
    assert len(outcome["radialMenu"]) == 4
    assert all(case["passed"] for case in outcome["radialMenu"])
    assert [case["effectiveSize"] for case in outcome["radialMenu"]] == pytest.approx(
        [48.0, 59.0, 74.0, 110.0], abs=0.1
    )
    assert all(case["gridMode"] for case in outcome["radialMenu"])
    assert all(case["collisions"] == [] for case in outcome["radialMenu"])
    assert all(
        center["passed"] and center["targets"] == [center["id"]]
        for case in outcome["radialMenu"]
        for center in case["uniqueCenters"]
    )
    assert outcome["dragEventTimerHandoff"]["passed"] is True
    assert outcome["staleLocalEventHandoff"]["passed"] is True
    assert outcome["qtestDragPressure"]["passed"] is True
    assert outcome["qtestDragPressure"]["qtScaleFactor"] == qt_scale_factor
    assert outcome["qtestDragPressure"]["sampleCount"] >= 70
    assert outcome["pointerHandlerFollow"]["passed"] is True
    assert outcome["pointerHandlerFollow"]["qtScaleFactor"] == qt_scale_factor
    assert outcome["pointerHandlerFollow"]["resizeHoverArmed"] is True
    assert outcome["pointerHandlerFollow"]["resizeReleased"] is True
    assert outcome["pointerHandlerFollow"]["resizeReleaseWrites"] == 1
    assert all(
        button["inside"]
        for case in outcome["radialMenu"]
        for button in case["buttons"]
    )
    assert len(outcome["focusAura"]) == 3
    assert all(case["passed"] for case in outcome["focusAura"])
    assert all(case["roundMarkers"] for case in outcome["focusAura"])
    assert [case["dpiScale"] for case in outcome["petHabitat"]] == [1.0, 1.5, 2.0]
    assert all(case["passed"] for case in outcome["petHabitat"])
    assert Path(outcome["report"]).is_file()
