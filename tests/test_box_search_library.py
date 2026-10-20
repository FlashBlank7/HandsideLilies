"""Large owned corpus correctness; service timings are diagnostic, not an FPS gate."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def test_large_library_search_limits_cancellation_and_worker_coalescing():
    root = Path(__file__).resolve().parents[1]
    report_path = root / "artifacts" / ("search-library-" + uuid.uuid4().hex[:8] + ".json")
    result = subprocess.run(
        [sys.executable, "-B", str(root / "scripts" / "measure_box_search_library.py"),
         "--output", str(report_path)],
        cwd=root, env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                           QML_DISABLE_DISK_CACHE="1"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] and len(report["checks"]) == 4 and all(report["checks"].values())
    assert report["corpus"] == {"messages": 20000, "reading_cards": 2000, "papers": 200,
                                "desktop_items": 300, "tasks": 500, "reminders": 500}
    assert report["serviceTiming"]["samples"] == len(report["timings"]) == 55
    assert report["serviceTiming"]["firstPassIsNotColdDisk"] is True
    assert report["worker"]["mainLoopTicksWhileHeld"] >= 3
    assert report["worker"]["distinctWorkers"] == 1
    assert report["worker"]["allCallsOffGuiThread"] is True
    assert report["worker"]["queriesExecuted"] == ["", "held-old-query", "图表定位-0177"]
    assert report["worker"]["notNativeResponsivenessProof"] is True
    for path, digest in report["sourceHashes"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest
