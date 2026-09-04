from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_connector_policy_and_safe_configuration_hydrate_independently() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_connector_policy_hydration.py"),
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
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {
        "calendarHydrated": True,
        "slackHydrated": True,
        "calendarDraftRestored": True,
        "slackDraftRestored": True,
        "saveIsolated": True,
        "canonicalRefreshPreservesEdits": True,
        "reopenKeepsDraft": True,
        "inactiveCanonicalRefresh": True,
        "calendarSaveIsolated": True,
        "passed": True,
    }
