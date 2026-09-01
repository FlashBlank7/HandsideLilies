from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_chat_restore_stability_probe_never_repeatedly_raises_a_windowed_tool() -> None:
    source = (PROJECT_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
    body = source[
        source.index("    function presentChatWindow(token, attempt) {") :
        source.index("    function presentChatPage(pageIndex) {")
    ]

    # One raise belongs to a real minimized-state repair and one to the
    # user's initial explicit presentation.  Subsequent Windowed probes only
    # count stable event turns; they must never keep stealing application
    # focus while WPS or another window is already active again.
    assert body.count("chatWindow.raise()") == 2
    assert "else if (attempt === 0)" in body
    assert "presentationStableChecks >= 10" in body
    assert "if (chatWindow.presentationRecoveryArmed && attempt < 24)" in body
    assert "interval: 80" in source
    assert body.rindex("chatWindow.requestActivate()") < body.index(
        "// On Windows a retained Qt.Tool"
    )


def test_compact_ui_primary_interactions_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_compact_ui.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # The verifier intentionally exercises compact startup, both desktop
        # renderer lifecycles, multi-monitor drag, companion, focus, nine
        # responsive habitat profiles and every radial entry in one Qt
        # process. Software rendering now sits near 60s on this Windows host;
        # retain bounded headroom without mistaking valid QML settling for a
        # hang.
        timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
