"""Isolated QML loop: no physical mouse, screen captures or desktop windows."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_pointer_bursts_commit_without_render_frames():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "verify_ui_input_pipeline.py")],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] and not report["framesRequired"]
    assert len(report["gestures"]) == 3
    assert all(item["burst"] == 3 and item["commits"] == 1 for item in report["gestures"])
