import json
import os
from pathlib import Path
import subprocess
import sys


def test_evidence_and_provided_text_real_qml_flow():
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_CONTROLS_STYLE="Basic", PYTHONUTF8="1")
    result = subprocess.run([sys.executable, str(root / "scripts" / "verify_awareness_ui.py")],
        cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    report = json.loads((root / "artifacts" / "awareness" / "result.json").read_text(encoding="utf-8"))
    assert report["passed"] and not report["warnings"]
    assert len(report["checks"]) == 5 and all(report["checks"].values())
