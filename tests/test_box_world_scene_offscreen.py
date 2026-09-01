from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_box_world_is_a_real_large_scene_with_restorable_entry() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_QPA_OFFSCREEN_SIZE"] = "1200x900"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_box_world_scene.py")],
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
    report = json.loads(completed.stdout)
    first = report["firstPresentation"]
    assert first["title"] == "莉莉丝 · 盒中世界"
    assert first["placedDecorations"] == [
        "boxWorldPlaced_box-core",
        "boxWorldPlaced_paper-shelf",
        "boxWorldPlaced_rest-cushion",
    ]
    assert first["stateRows"] == [
        "boxWorldObjectState_box-core",
        "boxWorldObjectState_paper-shelf",
        "boxWorldObjectState_rest-cushion",
        "boxWorldObjectState_workbench",
        "boxWorldObjectState_living-corner",
        "boxWorldObjectState_letter-rack",
    ]
    assert first["greetingFits"] is True
    assert report["repeatAfterMinimize"]["reachable"] is True
    assert report["narrowLayout"] == {
        "enabled": True,
        "size": [620, 470],
        "stageFound": True,
        "panelFound": True,
        "titleBarVisible": True,
        "footerVisible": True,
        "titleBarGeometry": [0, 0, 620, 72],
        "stageGeometry": [12, 14, 596, 338],
        "footerGeometry": [0, 438, 620, 32],
        "contentSize": [620, 470],
        "panelInsideStage": True,
        "greetingFits": True,
        "manageComfortable": True,
    }
    micro = report["microLayout"]
    assert micro["enabled"] is True
    assert micro["size"] == [360, 360]
    assert micro["stageFound"] is True
    assert micro["panelFound"] is True
    assert micro["panelHeight"] <= 98
    assert micro["panelInsideStage"] is True
    assert micro["titleFits"] is True
    assert micro["headerControlsInside"] is True
    assert micro["manageComfortable"] is True
    assert micro["characterVisibleRatio"] >= 0.45
    assert report["exit"] == {
        "signalCount": 1,
        "requestedVisible": False,
        "hidden": True,
    }


def test_box_world_scene_contract_is_independent_of_work_panel() -> None:
    source = (PROJECT_ROOT / "qml" / "V03BoxWorldScene.qml").read_text(encoding="utf-8")
    assert "Window {" in source
    assert 'objectName: "v03BoxWorldScene"' in source
    assert "property bool requestedVisible" in source
    assert "property bool microViewport" in source
    assert "property var presentationArea" in source
    assert "property var appBackend" in source
    assert "signal exitRequested()" in source
    assert "signal manageDecorationsRequested()" in source
    assert "V03WorkPanel" not in source
    assert "boxWorldSceneStage" in source
    assert "boxWorldLilithImage" in source
    main_source = (PROJECT_ROOT / "qml" / "Main.qml").read_text("utf-8")
    assert "stayOnTopWhenPresented: true" in main_source

    app_source = (PROJECT_ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    assert 'world = menu.addAction("进入盒中世界")' in app_source
    assert "world.triggered.connect(backend.enterBoxWorld)" in app_source
