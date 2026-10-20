import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def test_card_source_search_and_confirmation_in_production_qml():
    root = Path(__file__).resolve().parents[1]
    report_path = root / "artifacts" / ("card-source-ui-" + uuid.uuid4().hex[:8] + ".json")
    result = subprocess.run(
        [sys.executable, "-B", str(root / "scripts" / "verify_card_source_ui.py"), "--output", str(report_path)],
        cwd=root, env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", QML_DISABLE_DISK_CACHE="1"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] and not report["warnings"] and not report["error"]
    assert len(report["checks"]) == 11 and all(report["checks"].values())
