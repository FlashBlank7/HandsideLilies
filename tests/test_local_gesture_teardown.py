"""Production delegate teardown with local Qt events, never native input."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]


def test_production_local_gesture_teardown_matrix():
    report = ROOT / "artifacts" / f"local-gesture-matrix-{uuid.uuid4().hex[:8]}.json"
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/verify_local_gesture_teardown.py"),
         "--report", str(report)],
        cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["passed"] and len(data["results"]) == 21
    assert all(row["passed"] and not row["warnings"] for row in data["results"])


def test_delegate_finishes_original_owner_idempotently_even_without_native_claim():
    source = (ROOT / "qml/Main.qml").read_text(encoding="utf-8")
    section = source.split("id: componentMoveDrag", 1)[1].split("WheelHandler {", 1)[0]
    finish = section.split("function finishGestureOwnership(cancelled)", 1)[1].split(
        "function flushPendingTranslation", 1)[0]
    assert "if (!gestureStarted)" in finish
    assert finish.index("gestureStarted = false") < finish.index("petWindow.endLocalGesture(")
    assert "var owner = gestureOwner" in finish
    assert "String(modelData.action)" not in finish
    assert "if (nativeGestureClaimed)" not in finish
    assert "backend.setPetInteractionLock(owner, false)" in finish
    assert "finally" in finish
    assert "clearPetInteractionLocks" not in section
    assert "finishGestureOwnership(true)" in section.split("Component.onDestruction:", 1)[1]
