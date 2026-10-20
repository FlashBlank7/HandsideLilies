from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_grouped_pages_search_and_live_drafts_offscreen():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_ui_v0356.py")],
        cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True, encoding="utf-8", errors="replace", timeout=75,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["passed"] and len(report["checks"]) == 20
    assert report["draftSurvivesNavigation"] and report["inertRichText"]
