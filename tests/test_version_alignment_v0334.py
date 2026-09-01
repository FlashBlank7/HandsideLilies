from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.3.34"
HISTORICAL_V0333_SHA256 = (
    "672c13e50e5b84f8727b1207f2832c217304970f9da5b7ec359afcb7b7052c1a"
)
HISTORICAL_V0334_SHA256 = (
    "c0506816bd3d5325bf19a1f57b1af03a90edffd38848666b5d175ad54d0eb9d2"
)


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def _quoted_paths(block: str) -> set[str]:
    return set(re.findall(r"(?m)^\s*'([^']+)'\s*,?\s*$", block))


def _release_file_sets(wrapper: str) -> tuple[set[str], set[str]]:
    additional = wrapper[
        wrapper.index("$AdditionalFiles = @(") : wrapper.index("\n& $Engine `")
    ]
    fence = wrapper[
        wrapper.index("foreach ($relativePath in @(") : wrapper.rindex(")) {")
    ]
    return _quoted_paths(additional), _quoted_paths(fence)


def _legacy_v0334_active_version_points_are_aligned() -> None:
    exact_markers = {
        "pyproject.toml": 'version = "0.3.34"',
        "uv.lock": 'version = "0.3.34"',
        "src/lilies_in_the_box.egg-info/PKG-INFO": "Version: 0.3.34",
        "src/lilies/__init__.py": '__version__ = "0.3.34"',
        "src/lilies/app.py": 'app.setApplicationVersion("0.3.34")',
        "src/lilies/core/codex_subscription.py": 'CLIENT_VERSION = "0.3.34"',
        "scripts/install_windows.ps1": "version = '0.3.34'",
    }
    for relative, marker in exact_markers.items():
        source = _read(relative)
        assert marker in source, relative

    theme = json.loads(_read("themes/first-encounter/theme.json"))
    assert theme["version"] == RELEASE_VERSION

    version_info = _read("packaging/windows_version_info.txt")
    for marker in ("(0, 3, 34, 0)", '"0.3.34.0"', '"0.3.34"'):
        assert marker in version_info


def _legacy_v0334_current_diagnostic_report_pointers_are_aligned() -> None:
    pointers = {
        "scripts/verify_codex_subscription_smoke.py": ".codex-subscription-smoke-v0334",
        "scripts/verify_packaged_windows_startup.ps1": "packaged-windows-startup-v0334.json",
        "scripts/verify_packaged_compact_resources.ps1": "packaged-compact-resource-v0334.json",
        "scripts/verify_pose_click_masks.py": "pose-click-mask-v0334.json",
        "tests/test_pose_click_mask_qml_v0329.py": "pose-click-mask-v0334.json",
        "tests/test_windows_startup_probe_contract.py": "packaged-windows-startup-v0334.json",
    }
    for relative, marker in pointers.items():
        source = _read(relative)
        assert marker in source, relative

    compact = _read("scripts/verify_packaged_compact_resources.ps1")
    assert "$ExpectedApplicationVersion = '0.3.34'" in compact
    assert "packaged-self-test-v0334.json" in compact


def test_v0334_wrapper_versions_reports_and_immutable_history_are_exact() -> None:
    wrapper_path = PROJECT_ROOT / "scripts" / "promote_v0334.ps1"
    wrapper = wrapper_path.read_text(encoding="utf-8")
    old_path = PROJECT_ROOT / "scripts" / "promote_v0333.ps1"

    assert "$ReleaseVersion = '0.3.34'" in wrapper
    assert "$FileVersion = '0.3.34.0'" in wrapper
    assert "packaged-self-test-v0334.json" in wrapper
    assert "packaged-compact-resource-v0334.json" in wrapper
    assert "packaged-windows-startup-v0334.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0334.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0334.json'" in wrapper
    assert "'scripts\\promote_v0333.ps1'" in wrapper
    assert HISTORICAL_V0333_SHA256 in wrapper
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == HISTORICAL_V0333_SHA256
    assert hashlib.sha256(wrapper_path.read_bytes()).hexdigest() == HISTORICAL_V0334_SHA256


def test_v0334_promotion_copies_and_hash_fences_the_current_change_set() -> None:
    wrapper = _read("scripts/promote_v0334.ps1")
    additional, fence = _release_file_sets(wrapper)
    required = {
        "qml\\Main.qml",
        "qml\\CompanionBubble.qml",
        "qml\\V03ConnectorSetup.qml",
        "qml\\V03Dock.qml",
        "qml\\V03PetPoseResolver.qml",
        "qml\\V03WorkPanel.qml",
        "src\\lilies\\app.py",
        "src\\lilies\\backend.py",
        "src\\lilies\\companion_controller.py",
        "src\\lilies\\core\\activity.py",
        "src\\lilies\\core\\companion.py",
        "src\\lilies\\core\\companion_runtime.py",
        "src\\lilies\\core\\database.py",
        "src\\lilies\\core\\orchestration.py",
        "src\\lilies\\core\\pet_habitat.py",
        "scripts\\build_windows.ps1",
        "scripts\\verify_box_world_click_path.py",
        "scripts\\verify_entry_actions.py",
        "scripts\\verify_main_qml_ui.py",
        "scripts\\verify_dock_ui.py",
        "scripts\\verify_cross_dpi_layout.py",
        "scripts\\verify_companion_flow_ui.py",
        "scripts\\verify_companion_frequency_draft.py",
        "scripts\\verify_drag_runtime_v0325.py",
        "scripts\\verify_focus_timer_aura.py",
        "scripts\\verify_pet_pose_resolver.py",
        "scripts\\verify_pose_assets.py",
        "scripts\\verify_native_window_capture.py",
        "tests\\test_box_world_click_path_offscreen.py",
        "tests\\test_activity_context.py",
        "tests\\test_browser_capture_safety_v0332.py",
        "tests\\test_companion.py",
        "tests\\test_companion_controller.py",
        "tests\\test_companion_resilience_v0333.py",
        "tests\\test_companion_discoverability_qml.py",
        "tests\\test_companion_flow_ui_offscreen.py",
        "tests\\test_companion_native_presentation.py",
        "tests\\test_companion_frequency_draft_v0332.py",
        "tests\\test_cross_dpi_layout_v0312.py",
        "tests\\test_dock_ui_offscreen.py",
        "tests\\test_entry_actions_offscreen.py",
        "tests\\test_drag_runtime_v0325.py",
        "tests\\test_focus_timer_aura_qml.py",
        "tests\\test_outfit_asset_gate.py",
        "tests\\test_main_qml_ui_v0333.py",
        "tests\\test_optional_habitat_pose_gate_v0326.py",
        "tests\\test_pet_habitat_v03.py",
        "tests\\test_pet_pose_resolver_v0332.py",
        "tests\\test_pose_asset_gate.py",
        "tests\\test_release_focus_gate_v0332.py",
        "tests\\test_release_qt_cache_gate_v0332.py",
        "tests\\test_qt_cache_routing_v0332.py",
        "tests\\test_version_alignment_v0332.py",
        "tests\\test_release_focus_gate_v0333.py",
        "tests\\test_release_qt_cache_gate_v0333.py",
        "tests\\test_version_alignment_v0333.py",
        "tests\\test_native_window_capture_probe_v0334.py",
        "tests\\test_release_focus_gate_v0334.py",
        "tests\\test_release_qt_cache_gate_v0334.py",
        "tests\\test_version_alignment_v0334.py",
        "tests\\test_windows_installer_contract.py",
    }
    assert required <= additional
    assert required <= fence


def test_v0334_requires_packaged_resolver_cache_focus_native_and_pose_gates() -> None:
    wrapper = _read("scripts/promote_v0334.ps1")
    for marker in (
        "@('qml\\V03PetPoseResolver.qml', '_internal\\qml\\V03PetPoseResolver.qml')",
        "Assert-QtCacheRoutingReport $selfTest 'selfTest'",
        "Assert-QtCacheRoutingReport $windowsStartup 'windowsStartup'",
        "Assert-FocusTimerAnimationReport",
        "sequencesStrictlyIncreasing",
        "transitionSequences",
        "nativeRadialWorldHit",
        "nativeDesktopModeTabHit",
        "nativeTransparentCornerPass",
        "Assert-MatchingReleaseFile",
        "pose-click-mask-v0334.json",
        "verify_native_window_capture.py",
    ):
        assert marker in wrapper


def test_v0334_validate_only_and_formal_promotion_share_candidate_gates() -> None:
    wrapper = _read("scripts/promote_v0334.ps1")
    validate_only = wrapper.index("if ($ValidateOnly)")
    promotion = wrapper.index("\n& $Engine `")

    for marker in (
        "foreach ($relativePath in $AdditionalFiles)",
        "Assert-CandidateReleaseFile $relativePath",
        "$poseClickMask = Read-ReleaseJson $PoseClickMaskReport",
        "Assert-PoseClickMaskReport $poseClickMask",
        "Assert-ReleaseArtifactFreshness",
        "$nativeCaptureDiagnostic = Invoke-NativeCaptureDiagnostic",
    ):
        assert wrapper.index(marker) < validate_only
    assert validate_only < promotion
    assert "[int]$TimeoutMilliseconds = 15000" in wrapper
    assert "exactly the four content-free fields" in wrapper
    assert "Assert-JsonString $Report 'reasonCode' 'ok' 'nativeCapture'" in wrapper
    assert "Assert-JsonBoolean $Report 'passed' $true 'poseClickMask'" in wrapper


def _legacy_v0334_build_generates_all_release_evidence_in_dependency_order() -> None:
    build = _read("scripts/build_windows.ps1")
    steps = (
        "Start-Process -FilePath $CandidateExe",
        "verify_packaged_compact_resources.ps1",
        "verify_packaged_windows_startup.ps1",
        "verify_pose_click_masks.py",
    )

    offsets = [build.index(step) for step in steps]
    assert offsets == sorted(offsets)
    for report in (
        "packaged-self-test-v0334.json",
        "packaged-compact-resource-v0334.json",
        "packaged-windows-startup-v0334.json",
        "pose-click-mask-v0334.json",
    ):
        assert report in build
    assert build.index("Remove-StaleReleaseArtifact $artifact") < offsets[0]
    assert "-WindowStyle Hidden -PassThru" in build
    assert "$selfTestProcess.WaitForExit(60000)" in build
    assert "Stop-Process -Id $selfTestProcess.Id -Force" in build


def _legacy_v0334_readme_documents_one_click_and_equivalent_sequential_gates() -> None:
    readme = _read("README.md")
    build = readme.index(".\\scripts\\build_windows.ps1")
    self_test = readme.index("--self-test", build)
    compact = readme.index("verify_packaged_compact_resources.ps1", self_test)
    startup = readme.index("verify_packaged_windows_startup.ps1", compact)
    pose = readme.index("verify_pose_click_masks.py", startup)
    validate = readme.index("promote_v0334.ps1 -ValidateOnly", pose)

    assert build < self_test < compact < startup < pose < validate


def test_v0334_readme_history_and_reference_art_stay_non_runtime() -> None:
    readme = _read("README.md")
    current = "### v0.3.34"
    historical = "### v0.3.33"
    assert readme.index(current) < readme.index(historical)
    current_section = readme[readme.index(current) : readme.index(historical)]
    for marker in (
        "原生窗口捕获",
        "内容无关",
        "发布脚手架",
        "v0.3.34",
    ):
        assert marker in current_section

    spec = _read("LiliesInTheBox.spec")
    theme = _read("themes/first-encounter/theme.json")
    draft_name = "lilith-pose-micro-corner-grip-draft-v6-baked-checkerboard.png"
    wrapper = _read("scripts/promote_v0334.ps1")
    assert "art-reference" not in spec
    assert draft_name not in theme
    assert f"art-reference\\generated-v0.3\\{draft_name}" in wrapper
    assert f"themes\\first-encounter\\assets\\{draft_name}" not in wrapper
