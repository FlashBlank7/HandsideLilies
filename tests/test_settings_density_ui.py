import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scale", ["1", "2"])
def test_minimum_size_settings_and_long_records_offscreen(scale):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "verify_settings_density_ui.py")],
        cwd=root, env={**os.environ, "PYTHONUTF8": "1", "QT_SCALE_FACTOR": scale},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["passed"] and not report["warnings"]
    assert len(report["checks"]) == 5 and all(report["checks"].values())
