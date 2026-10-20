from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_explicit_paper_source_confirmation_real_qml():
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QSG_RHI_BACKEND="software",
                       QML_DISABLE_DISK_CACHE="1", PYTHONUTF8="1")
    result = subprocess.run([sys.executable, str(root / "scripts" / "verify_paper_source_confirmation_ui.py")],
        cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    report = json.loads((root / "artifacts" / "paper-source-confirmation" / "result.json").read_text(encoding="utf-8"))
    assert report["passed"] and not report["warnings"]
    assert len(report["checks"]) == 5 and all(report["checks"].values())
