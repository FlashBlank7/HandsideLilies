from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_main_qml_copy_and_local_paper_controls_are_explicit() -> None:
    qml = (PROJECT_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
    companion = (
        PROJECT_ROOT / "src" / "lilies" / "companion_controller.py"
    ).read_text(encoding="utf-8")

    assert "打开应用、文件夹、文件或网页：…" in qml
    assert "或输入执行命令" not in qml
    assert "component LiliesPaperButton: Button" in qml
    assert "component LiliesPaperScrollBar: ScrollBar" in qml
    assert 'objectName: "companionMarkUnreadReadButton"' in qml
    assert "backend.companionService.markUnreadRead()" in qml
    assert "def markUnreadRead(self) -> bool:" in companion
    assert "Number(chatWindow.page) === 3" in qml
    assert "function packedActionX(index, desired, itemWidth)" in qml
    assert "function packedActionY(index, desired, itemHeight)" in qml


def test_main_qml_settings_geometry_and_paper_style_offscreen() -> None:
    environment = dict(os.environ)
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QSG_RHI_BACKEND": "software",
            "PYTHONUTF8": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_main_qml_ui.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["passed"] is True
    assert report["companion"]["inside"] is True
    assert report["companion"]["contentWidth"] == report["companion"]["availableWidth"]
    assert report["settings"]["inside"] is True
    assert report["settings"]["contentWidth"] == report["settings"]["availableWidth"]
    assert all(
        not swatch["opaqueBlack"] for swatch in report["paperColors"].values()
    )
