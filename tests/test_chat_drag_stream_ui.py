import json
import os
from pathlib import Path
import subprocess
import sys


def test_production_gesture_and_chat_document_coalescing():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-B", str(root / "scripts" / "verify_chat_drag_stream_ui.py")],
        cwd=root, env=dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_CONTROLS_STYLE="Basic",
                           QML_DISABLE_DISK_CACHE="1", PYTHONUTF8="1"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    report = json.loads((root / "artifacts" / "chat-drag-stream-ui" / "result.json").read_text(encoding="utf-8"))
    assert report["passed"] and not report["warnings"]
    assert len(report["checks"]) == 7 and all(report["checks"].values())
