from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from lilies.app import packaged_compact_startup_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1, f"expected one {name!r} function, found {len(matches)}"
    segment = ast.get_source_segment(source, matches[0])
    assert segment is not None
    return segment


class _SelfTestWindow:
    def __init__(
        self,
        object_name: str,
        *,
        persistent_graphics: bool = False,
        persistent_scene_graph: bool = False,
    ) -> None:
        self._object_name = object_name
        self.persistent_graphics = persistent_graphics
        self.persistent_scene_graph = persistent_scene_graph

    def objectName(self) -> str:
        return self._object_name

    def isPersistentGraphics(self) -> bool:
        return self.persistent_graphics

    def isPersistentSceneGraph(self) -> bool:
        return self.persistent_scene_graph


class _SelfTestPet:
    def __init__(self, *, low_power: object = True, target_fps: object = 15) -> None:
        self.values = {"lowPower": low_power, "targetFps": target_fps}

    def property(self, name: str) -> object:
        return self.values.get(name)


def _runtime_snapshot(**scene_overrides: object) -> dict[str, object]:
    scene = {
        "scene2dLoaded": False,
        "videoLoaded": False,
        "videoPlaybackState": "unloaded",
        "mutableModuleDetail": "must-not-leak",
    }
    scene.update(scene_overrides)
    return {
        "schemaVersion": 99,
        "shellMode": "compact",
        "renderer": "video",
        "scene": scene,
        "loadedModules": {"must-not-leak.dll": True},
    }


def test_packaged_compact_startup_contract_is_strict_and_projected() -> None:
    windows = [_SelfTestWindow("")] + [
        _SelfTestWindow(f"quickWindow{index}") for index in range(1, 16)
    ]
    lifecycle = SimpleNamespace(windows=tuple(windows))

    contract = packaged_compact_startup_contract(
        lifecycle,
        _SelfTestPet(),
        _runtime_snapshot(),
    )

    assert contract["passed"] is True
    assert contract["shellMode"] == "compact"
    quick_windows = contract["quickWindows"]
    assert quick_windows["expectedWindowCount"] == 16
    assert quick_windows["windowCount"] == 16
    assert quick_windows["persistentHintsDisabled"] is True
    assert quick_windows["passed"] is True
    assert len(quick_windows["persistentHints"]) == 16
    assert quick_windows["persistentHints"][0]["objectName"] == "desktopRootWindow"
    assert all(
        hint["persistentGraphics"] is False
        and hint["persistentSceneGraph"] is False
        for hint in quick_windows["persistentHints"]
    )
    assert contract["compactIdle"] == {
        "lowPower": True,
        "targetFps": 15,
        "passed": True,
    }
    assert contract["sceneLoaders"] == {
        "scene2dLoaded": False,
        "videoLoaded": False,
        "videoPlaybackState": "unloaded",
        "passed": True,
    }
    serialized = json.dumps(contract, ensure_ascii=False)
    assert "mutableModuleDetail" not in serialized
    assert "loadedModules" not in serialized
    assert "renderer" not in serialized


def test_packaged_compact_startup_contract_fails_closed() -> None:
    windows = [_SelfTestWindow("")] + [
        _SelfTestWindow(f"quickWindow{index}") for index in range(1, 16)
    ]
    lifecycle = SimpleNamespace(windows=tuple(windows))

    windows[4].persistent_graphics = True
    persistent_failure = packaged_compact_startup_contract(
        lifecycle,
        _SelfTestPet(),
        _runtime_snapshot(),
    )
    assert persistent_failure["quickWindows"]["passed"] is False
    assert persistent_failure["passed"] is False

    windows[4].persistent_graphics = False
    idle_failure = packaged_compact_startup_contract(
        lifecycle,
        _SelfTestPet(low_power=False, target_fps=60),
        _runtime_snapshot(),
    )
    assert idle_failure["compactIdle"]["passed"] is False
    assert idle_failure["passed"] is False

    loader_failure = packaged_compact_startup_contract(
        lifecycle,
        _SelfTestPet(),
        _runtime_snapshot(videoLoaded=True, videoPlaybackState="playing"),
    )
    assert loader_failure["sceneLoaders"]["passed"] is False
    assert loader_failure["passed"] is False


def test_packaged_self_test_is_intrinsically_nonvisual() -> None:
    source = _read("src/lilies/app.py")
    safety = source.index("# A packaged self-test is an unattended release probe")
    application = source.index("app = QApplication(")

    assert safety < application
    startup = source[safety:application]
    assert 'os.environ["QT_QPA_PLATFORM"] = "offscreen"' in startup
    assert 'os.environ["QT_QPA_OFFSCREEN_SIZE"] = "1200x900"' in startup
    assert 'os.environ["QSG_RHI_BACKEND"] = "software"' in startup
    assert 'os.environ["QT_QUICK_BACKEND"] = "software"' in startup

    self_test = source[source.index("    if args.self_test:", application) :]
    assert "grabWindow" not in self_test
    assert "primaryScreen" not in self_test
    assert "QtTest" not in self_test
    assert "QTest" not in self_test
    assert "QQmlComponent(" not in self_test
    assert "createWindow(" not in self_test
    assert re.search(r"\b(?:QQuickWindow|QWindow)\s*\(", self_test) is None


def test_packaged_self_test_drives_the_real_focus_animation_lifecycle() -> None:
    source = _read("src/lilies/app.py")
    self_test = source[
        source.index("    if args.self_test:", source.index("backend.enter_initial_mode()")) :
    ]

    begin = _function_source(source, "begin_focus_probe")
    assert begin.index("backend.focusStart(5)") < begin.index(
        "QTimer.singleShot(180, capture_focus_started)"
    )

    started = _function_source(source, "capture_focus_started")
    assert 'focus_timer_animation["started"] = started_stage' in started
    assert started.index('focus_timer_animation["started"]') < started.index(
        "begin_focus_pause()"
    )

    pause = _function_source(source, "begin_focus_pause")
    assert pause.index("backend.focusPause()") < pause.index(
        "QTimer.singleShot(80, capture_focus_pause_baseline)"
    )
    pause_baseline = _function_source(source, "capture_focus_pause_baseline")
    assert "motionTickCount" in pause_baseline
    assert "focus_aura_surface.property(\"scale\")" in pause_baseline
    assert "QTimer.singleShot(160, capture_focus_paused)" in pause_baseline

    paused = _function_source(source, "capture_focus_paused")
    assert 'focus_timer_animation["paused"] = paused_stage' in paused
    assert paused.index('focus_timer_animation["paused"]') < paused.index(
        "begin_focus_resume()"
    )

    resume = _function_source(source, "begin_focus_resume")
    assert resume.index("backend.focusResume()") < resume.index(
        "QTimer.singleShot(180, capture_focus_resumed)"
    )
    resumed = _function_source(source, "capture_focus_resumed")
    assert 'focus_timer_animation["resumed"] = resumed_stage' in resumed
    assert resumed.index('focus_timer_animation["resumed"]') < resumed.index(
        "begin_focus_finish()"
    )

    finish = _function_source(source, "begin_focus_finish")
    assert finish.index("backend.focusFinish()") < finish.index(
        "QTimer.singleShot(120, finish_focus_probe)"
    )
    finished = _function_source(source, "finish_focus_probe")
    assert 'focus_timer_animation["finished"] = finished_stage' in finished
    assert 'for name in ("started", "paused", "resumed", "finished")' in finished
    assert 'focus_timer_animation["sequencesStrictlyIncreasing"]' in finished
    assert 'focus_timer_animation["passed"] = bool(' in finished

    assert '"focusTimerAnimation": focus_timer_animation' in self_test
    assert '"focusTimerAnimationPassed": bool(' in self_test
    assert 'focus_timer_animation.get("passed", False)' in self_test


def test_packaged_focus_probe_keeps_the_global_timeout_armed() -> None:
    source = _read("src/lilies/app.py")
    self_test = source[
        source.index("    if args.self_test:", source.index("backend.enter_initial_mode()")) :
    ]
    finish_self_test = _function_source(source, "finish_self_test")
    finish_self_test_prelude = finish_self_test.split(
        "\n            def write_result", 1
    )[0]
    write_result = _function_source(source, "write_result")
    fail_self_test = _function_source(source, "fail_self_test")

    # Receiving the chat reply only starts the asynchronous focus chain. It must
    # not mark the self-test finished and thereby disarm the 8 second watchdog.
    assert (
        'if finished["value"] or self_test_started["value"]:'
        in finish_self_test_prelude
    )
    assert 'self_test_started["value"] = True' in finish_self_test_prelude
    assert 'finished["value"] = True' not in finish_self_test_prelude

    assert 'if finished["value"]:' in write_result
    assert 'finished["value"] = True' in write_result
    assert 'if finished["value"]:' in fail_self_test
    assert 'finished["value"] = True' in fail_self_test
    assert '"error": "timeout"' in fail_self_test
    assert "QTimer.singleShot(8000, fail_self_test)" in self_test


def test_packaged_self_test_evidence_is_versioned_and_fails_closed() -> None:
    source = _read("src/lilies/app.py")
    identity = _function_source(source, "self_test_identity")
    write_result = _function_source(source, "write_result")
    fail_self_test = _function_source(source, "fail_self_test")

    for field in (
        '"schemaVersion": 1',
        '"applicationVersion": app.applicationVersion()',
        '"executableSha256": hashlib.sha256(',
        '"capturedAt": datetime.now(UTC).isoformat()',
    ):
        assert field in identity

    assert 'result = {\n                    **self_test_identity(),' in write_result
    assert 'result["passed"] = bool(' in write_result
    assert 'app.exit(0 if result["passed"] else 1)' in write_result
    assert "app.quit()" not in write_result

    assert "**self_test_identity()" in fail_self_test
    assert '"qmlLoaded": True' in fail_self_test
    assert '"identityPassed": False' in fail_self_test
    assert '"passed": False' in fail_self_test
    assert '"error": "timeout"' in fail_self_test
    assert "app.exit(1)" in fail_self_test
    assert "app.quit()" not in fail_self_test


def test_packaged_self_test_exercises_visible_user_flow_surfaces() -> None:
    source = _read("src/lilies/app.py")
    self_test = source[source.index("    if args.self_test:", source.index("backend.enter_initial_mode()")) :]

    for contract in (
        '"boxWorldSceneWindow"',
        '"boxWorldSceneStage"',
        "backend.enterBoxWorld()",
        '"companionBubbleWindow"',
        '"companionBodyText"',
        "backend.companion.bubbleChanged.emit()",
        '"boxWorldPresentationPassed"',
        '"syntheticProactiveBubbleVisible"',
        '"nativeSystemMovePathPresent"',
        '"dragFallbackVerified"',
    ):
        assert contract in self_test

    assert '"nativeSystemMoveRuntimeVerified": False' in self_test
    assert 'pet_window.property("nativeMoveController")' in self_test
    assert 'getattr(native_move_controller, "tryStartSystemMove", None)' in self_test
    assert "pet_window.followPointerAt(" in self_test
    assert "box_world_scene.isVisible()" in self_test
    assert "box_world_scene.isExposed()" in self_test
    assert "companion_bubble.isVisible()" in self_test
    assert "companion_bubble.isExposed()" in self_test
    assert "packaged_compact_startup_contract(" in self_test
    assert '"compactStartup": compact_startup' in self_test
    assert '"compactStartupPassed": bool(compact_startup["passed"])' in self_test


def test_v0320_release_gate_consumes_each_new_packaged_probe() -> None:
    wrapper = _read("scripts/promote_v0320.ps1")

    for required_true in (
        "boxWorldPresentationPassed",
        "syntheticProactiveBubbleVisible",
        "nativeSystemMovePathPresent",
        "dragFallbackVerified",
    ):
        assert f"'{required_true}'" in wrapper

    assert "Packaged self-test must run on the offscreen platform." in wrapper
    assert "selfTest.boxWorldPresentation.presentationCount" in wrapper
    assert "selfTest.syntheticProactiveBubble.presentationRevision" in wrapper
    assert "'packaged proactive bubble probe'" in wrapper
    assert (
        "Assert-JsonBoolean $selfTest 'nativeSystemMoveRuntimeVerified' $false"
        in wrapper
    )
    assert "Assert-JsonBoolean $selfTest.dragProbe $name $false" in wrapper
    assert "'compactStartupPassed'," in wrapper
    assert "Assert-JsonBoolean $selfTest $name $true 'selfTest'" in wrapper
    assert "selfTest.compactStartup.quickWindows.$name must be the integer 16." in wrapper
    assert "persistentHints must contain 16 windows" in wrapper
    assert "Assert-JsonBoolean $hint 'persistentGraphics' $false" in wrapper
    assert "Assert-JsonBoolean $hint 'persistentSceneGraph' $false" in wrapper
    assert "Assert-JsonBoolean $compactIdle 'lowPower' $true" in wrapper
    assert "compactIdle.targetFps must be the integer 15" in wrapper
    assert "Assert-JsonBoolean $sceneLoaders 'scene2dLoaded' $false" in wrapper
    assert "Assert-JsonBoolean $sceneLoaders 'videoLoaded' $false" in wrapper
    assert "sceneLoaders.videoPlaybackState must be unloaded" in wrapper
