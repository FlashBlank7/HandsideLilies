from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_suppressed_surface_requests_replay_once_when_safe_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_desktop_surface_replay_v0328.py"),
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
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])
    assert outcome["passed"] is True
    assert outcome["suppressedVisual"] == {
        "mode": "visual",
        "dockSuppressed": True,
        "pending": True,
        "replays": 0,
        "nativeRequests": 0,
        "petHidden": True,
    }
    assert outcome["restoredVisual"] == {
        "mode": "visual",
        "pending": False,
        "replays": 1,
        "nativeRequests": 1,
        "desktopVisible": True,
        "desktopExposed": True,
        "petVisible": True,
        "floatMode": "always",
    }
    assert outcome["duplicateSafe"] == {"replays": 1, "nativeRequests": 1}
    # The shortened visual-mode health timer deliberately adds native probe
    # requests before compact mode begins.  The replay invariant is that the
    # compact request adds none, not that the process-wide lifetime total is
    # still the single initial presentation request.
    compact_request_baseline = outcome["compactHealthProbe"]["nativeRequests"]
    assert compact_request_baseline == outcome["visualHealthProbe"]["after"]
    assert outcome["compactHealthProbe"]["requestsStable"] is True
    assert outcome["restoredCompact"] == {
        "mode": "compact",
        "pendingWasSet": True,
        "pending": False,
        "replays": 2,
        "nativeRequests": compact_request_baseline,
        "desktopHidden": True,
        "petVisible": True,
    }
