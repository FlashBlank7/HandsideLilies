"""A full pinned menu must not turn optional library functions into dead rows."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_library_mouse_entrances_with_all_pin_slots_occupied():
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, str(root / "scripts/verify_function_library_navigation.py")],
        cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["passed"] and report["pinSelectionPreserved"]
    assert len(report["clicks"]) == 4 and all(case["passed"] for case in report["clicks"])
    assert report["qmlWarnings"] == []
