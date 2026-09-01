from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

import scripts.verify_compact_resources as compact_resource_verifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_NAMED_WINDOWS = [
    "boxWorldSceneWindow",
    "chatWindow",
    "companionBubbleWindow",
    "focusDiversionBubbleWindow",
    "paperDock",
    "petWindow",
    "selectionBubble",
    "selectionQuestion",
    "v03AllWindowsDrawer",
    "v03ConnectorSetup",
    "v03FocusTimerAura",
    "v03PaperFoldDock",
    "v03WindowPreviewShelf",
    "v03WorkPanel",
    "windowShelf",
]


class _FakeWindow:
    def __init__(
        self,
        name: str,
        *,
        persistent_graphics: bool = False,
        persistent_scene_graph: bool = False,
    ) -> None:
        self.name = name
        self.persistent_graphics = persistent_graphics
        self.persistent_scene_graph = persistent_scene_graph

    def objectName(self) -> str:
        return self.name

    def isPersistentGraphics(self) -> bool:
        return self.persistent_graphics

    def isPersistentSceneGraph(self) -> bool:
        return self.persistent_scene_graph


def test_window_resource_lifecycle_calls_configurator_and_controls_passed(
    monkeypatch,
) -> None:
    root = object()
    windows = [_FakeWindow(name) for name in EXPECTED_NAMED_WINDOWS]
    windows.append(_FakeWindow(""))
    calls: list[object] = []

    def configure(value: object) -> SimpleNamespace:
        calls.append(value)
        return SimpleNamespace(windows=tuple(windows))

    monkeypatch.setattr(
        compact_resource_verifier,
        "configure_quick_window_resource_lifecycle",
        configure,
    )
    lifecycle = compact_resource_verifier.evaluate_window_resource_lifecycle(root)

    assert calls == [root]
    assert lifecycle["passed"] is True
    assert lifecycle["windowCount"] == lifecycle["expectedWindowCount"] == 16
    assert lifecycle["namedWindows"] == lifecycle["expectedNamedWindows"]
    assert lifecycle["namedWindows"] == EXPECTED_NAMED_WINDOWS
    assert lifecycle["persistentHintsDisabled"] is True
    assert len(lifecycle["persistentHints"]) == 16
    assert all(
        value["persistentGraphics"] is False
        and value["persistentSceneGraph"] is False
        for value in lifecycle["persistentHints"]
    )
    assert compact_resource_verifier.compact_resource_report_passed(
        True, True, True, True, lifecycle
    ) is True

    windows[0].persistent_graphics = True
    failed_lifecycle = compact_resource_verifier.evaluate_window_resource_lifecycle(
        root
    )
    assert calls == [root, root]
    assert failed_lifecycle["persistentHintsDisabled"] is False
    assert failed_lifecycle["passed"] is False
    assert compact_resource_verifier.compact_resource_report_passed(
        True, True, True, True, failed_lifecycle
    ) is False


def test_compact_unloads_desktop_scene_and_multimedia() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["QT_QUICK_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_compact_resources.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(
        (PROJECT_ROOT / "artifacts" / "compact-resource-lifecycle.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["passed"] is True
    assert report["platform"] == "offscreen"
    assert report["compactUnloaded"] is True
    assert report["coldMultimediaUnloaded"] is True
    assert report["visualRoundTrip"] is True
    assert report["visualSurfaceRecovery"] is True
    lifecycle = report["windowResourceLifecycle"]
    assert lifecycle["passed"] is True
    assert lifecycle["windowCount"] == lifecycle["expectedWindowCount"] == 16
    assert lifecycle["namedWindows"] == lifecycle["expectedNamedWindows"]
    assert lifecycle["namedWindows"] == EXPECTED_NAMED_WINDOWS
    assert lifecycle["persistentHintsDisabled"] is True
    assert len(lifecycle["persistentHints"]) == 16
    assert all(
        value["persistentGraphics"] is False
        and value["persistentSceneGraph"] is False
        for value in lifecycle["persistentHints"]
    )
    assert report["states"]["visualScene"]["sceneExists"] is True
    assert report["states"]["visualSceneRecovered"]["desktopVisible"] is True
    assert report["states"]["visualSceneRecovered"]["sceneExists"] is True
    assert report["states"]["visualVideo"]["playerExists"] is True
    for key in (
        "compactCold",
        "compactAfterScene",
        "compactVideoCold",
        "compactAfterVideo",
    ):
        state = report["states"][key]
        assert state["sceneExists"] is False
        assert state["playerExists"] is False
        assert state["playerState"] == "unloaded"
