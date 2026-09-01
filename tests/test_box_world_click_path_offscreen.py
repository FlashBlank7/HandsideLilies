from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_qml_pet_click_routes_reach_all_surfaces_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["QT_QUICK_BACKEND"] = "software"
    environment["QT_QPA_OFFSCREEN_SIZE"] = "1200x900"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_box_world_click_path.py"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(completed.stdout)
    assert report["passed"] is True
    assert report["statusFeedback"] == {
        "found": True,
        "visible": True,
        "text": "Offscreen status feedback",
        "doesNotTakeFocus": True,
        "passesThrough": True,
    }
    for mode in ("always", "normal", "minimumSize"):
        route = report[mode]
        assert route["bubbleVisibleBeforeMenu"] is True
        assert route["petLowPowerBeforeMenu"] is True
        assert route["menu"]["characterFilterHit"] is True
        assert route["menu"]["transparentCornerPassesThrough"] is True
        assert route["menu"]["menuExpanded"] is True
        assert route["menu"]["transitionHitContract"]["visibleSamples"] > 0
        assert route["menu"]["transitionHitContract"]["mismatches"] == []
        assert route["worldClick"]["offscreenRouteUnobstructed"] is True
        assert route["presentation"]["sceneExposed"] is True
        assert route["close"]["petReturned"] is True
        assert route["close"]["petDidNotTakeFocus"] is True

    assert report["minimumSize"]["worldClick"]["filterHit"] is True
    assert report["minimumSize"]["presentation"]["backendOpen"] is True
    assert report["unreadCue"] == {
        "found": True,
        "visible": True,
        "filterHit": True,
        "clicked": True,
        "openedStatus": True,
        "page": 3,
    }

    assert report["chat"]["opened"] is True
    assert report["chat"]["page"] == 0
    assert report["settings"]["opened"] is True
    assert report["settings"]["page"] == 4
    assert report["settings"]["functionLibrary"]["workSelected"] is True
    assert report["settings"]["functionLibrary"]["firstOptionalCannotMoveUp"] is True
    assert report["settings"]["functionLibrary"]["firstOptionalCanMoveDown"] is True
    assert report["settings"]["functionLibrary"]["lastOptionalCanMoveUp"] is True
    assert report["settings"]["functionLibrary"]["lastOptionalCannotMoveDown"] is True
    assert report["functionLibraryAction"]["opened"] is True
    assert report["functionLibraryAction"]["section"] == "work"

    main_source = (PROJECT_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
    assert "readonly property bool actionsInteractive: actionsVisible" in main_source
    assert "visible: compactWindow.actionsVisible" in main_source
    assert (
        "enabled: componentButton.visible && compactWindow.actionsInteractive"
        in main_source
    )
