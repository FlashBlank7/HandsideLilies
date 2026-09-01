from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_RELEASE_VERSION = "0.3.32"
HISTORICAL_V0331_SHA256 = (
    "8dddd05c4b5b97e3d589853785323b5e1426d974389ef492601e56f560f8feed"
)
HISTORICAL_V0332_SHA256 = (
    "8077086c0b8c450633a44b73808593a1b0d6d3ef5425cdd7decad3d7226d1a2f"
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


def test_v0332_wrapper_remains_an_immutable_historical_release_contract() -> None:
    path = PROJECT_ROOT / "scripts" / "promote_v0332.ps1"
    wrapper = path.read_text(encoding="utf-8")

    assert hashlib.sha256(path.read_bytes()).hexdigest() == HISTORICAL_V0332_SHA256
    assert "$ReleaseVersion = '0.3.32'" in wrapper
    assert "$FileVersion = '0.3.32.0'" in wrapper
    assert "packaged-self-test-v0332.json" in wrapper
    assert "packaged-compact-resource-v0332.json" in wrapper
    assert "packaged-windows-startup-v0332.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0332.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0332.json'" in wrapper
    assert "$ReleaseVersion = '0.3.33'" not in wrapper
    assert "packaged-self-test-v0333.json" not in wrapper


def test_v0333_release_keeps_the_v0332_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0333.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0332.ps1'" in current_wrapper
    assert "'tests\\test_version_alignment_v0332.py'" in current_wrapper
    assert HISTORICAL_V0332_SHA256 in current_wrapper
    assert readme.index("### v0.3.33") < readme.index("### v0.3.32")


def _legacy_v0332_active_version_points_are_aligned() -> None:
    exact_markers = {
        "pyproject.toml": 'version = "0.3.32"',
        "uv.lock": 'version = "0.3.32"',
        "src/lilies_in_the_box.egg-info/PKG-INFO": "Version: 0.3.32",
        "src/lilies/__init__.py": '__version__ = "0.3.32"',
        "src/lilies/app.py": 'app.setApplicationVersion("0.3.32")',
        "src/lilies/core/codex_subscription.py": 'CLIENT_VERSION = "0.3.32"',
        "scripts/install_windows.ps1": "version = '0.3.32'",
    }
    for relative, marker in exact_markers.items():
        source = _read(relative)
        assert marker in source, relative

    theme = json.loads(_read("themes/first-encounter/theme.json"))
    assert theme["version"] == HISTORICAL_RELEASE_VERSION

    version_info = _read("packaging/windows_version_info.txt")
    for marker in ("(0, 3, 32, 0)", '"0.3.32.0"', '"0.3.32"'):
        assert marker in version_info


def _legacy_v0332_current_diagnostic_report_pointers_are_aligned() -> None:
    pointers = {
        "scripts/verify_codex_subscription_smoke.py": ".codex-subscription-smoke-v0332",
        "scripts/verify_packaged_windows_startup.ps1": "packaged-windows-startup-v0332.json",
        "scripts/verify_packaged_compact_resources.ps1": "packaged-compact-resource-v0332.json",
        "scripts/verify_pose_click_masks.py": "pose-click-mask-v0332.json",
        "tests/test_pose_click_mask_qml_v0329.py": "pose-click-mask-v0332.json",
        "tests/test_windows_startup_probe_contract.py": "packaged-windows-startup-v0332.json",
    }
    for relative, marker in pointers.items():
        source = _read(relative)
        assert marker in source, relative

    compact = _read("scripts/verify_packaged_compact_resources.ps1")
    assert "$ExpectedApplicationVersion = '0.3.32'" in compact
    assert "packaged-self-test-v0332.json" in compact


def _legacy_v0332_wrapper_versions_reports_and_immutable_history_are_exact() -> None:
    wrapper = _read("scripts/promote_v0332.ps1")
    old_path = PROJECT_ROOT / "scripts" / "promote_v0331.ps1"

    assert "$ReleaseVersion = '0.3.32'" in wrapper
    assert "$FileVersion = '0.3.32.0'" in wrapper
    assert "packaged-self-test-v0332.json" in wrapper
    assert "packaged-compact-resource-v0332.json" in wrapper
    assert "packaged-windows-startup-v0332.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0332.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0332.json'" in wrapper
    assert "'scripts\\promote_v0331.ps1'" in wrapper
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == HISTORICAL_V0331_SHA256


def _legacy_v0332_promotion_copies_and_hash_fences_the_current_change_set() -> None:
    wrapper = _read("scripts/promote_v0332.ps1")
    additional, fence = _release_file_sets(wrapper)
    required = {
        "qml\\Main.qml",
        "qml\\CompanionBubble.qml",
        "qml\\V03PetPoseResolver.qml",
        "src\\lilies\\app.py",
        "src\\lilies\\companion_controller.py",
        "src\\lilies\\core\\activity.py",
        "src\\lilies\\core\\companion.py",
        "src\\lilies\\core\\companion_runtime.py",
        "src\\lilies\\core\\database.py",
        "src\\lilies\\core\\orchestration.py",
        "src\\lilies\\core\\pet_habitat.py",
        "scripts\\verify_box_world_click_path.py",
        "scripts\\verify_companion_frequency_draft.py",
        "scripts\\verify_drag_runtime_v0325.py",
        "scripts\\verify_focus_timer_aura.py",
        "scripts\\verify_pet_pose_resolver.py",
        "scripts\\verify_pose_assets.py",
        "tests\\test_box_world_click_path_offscreen.py",
        "tests\\test_activity_context.py",
        "tests\\test_browser_capture_safety_v0332.py",
        "tests\\test_companion.py",
        "tests\\test_companion_controller.py",
        "tests\\test_companion_discoverability_qml.py",
        "tests\\test_companion_frequency_draft_v0332.py",
        "tests\\test_drag_runtime_v0325.py",
        "tests\\test_focus_timer_aura_qml.py",
        "tests\\test_outfit_asset_gate.py",
        "tests\\test_optional_habitat_pose_gate_v0326.py",
        "tests\\test_pet_habitat_v03.py",
        "tests\\test_pet_pose_resolver_v0332.py",
        "tests\\test_pose_asset_gate.py",
        "tests\\test_release_focus_gate_v0332.py",
        "tests\\test_release_qt_cache_gate_v0332.py",
        "tests\\test_qt_cache_routing_v0332.py",
        "tests\\test_version_alignment_v0332.py",
    }
    assert required <= additional
    assert required <= fence


def _legacy_v0332_requires_packaged_resolver_cache_focus_native_and_pose_gates() -> None:
    wrapper = _read("scripts/promote_v0332.ps1")
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
        "pose-click-mask-v0332.json",
    ):
        assert marker in wrapper


def _legacy_v0332_readme_is_current_and_reference_art_stays_non_runtime() -> None:
    readme = _read("README.md")
    current = "### v0.3.32"
    historical = "### v0.3.31"
    assert readme.index(current) < readme.index(historical)
    current_section = readme[readme.index(current) : readme.index(historical)]
    for marker in (
        "V03PetPoseResolver",
        "哲思",
        "视觉锚点",
        "近似去重",
        "variation_nonce",
        "请求内加盐",
        "自定义频率",
        "直接键入",
        "编辑锁",
        "隐藏页面",
        "外部频率变更",
        "浏览器",
        "单次授权",
        "超宽",
        "拖动",
        "未读",
        "59/60/61",
        "v0.3.32",
    ):
        assert marker in current_section

    spec = _read("LiliesInTheBox.spec")
    theme = _read("themes/first-encounter/theme.json")
    draft_name = "lilith-pose-micro-corner-grip-draft-v6-baked-checkerboard.png"
    wrapper = _read("scripts/promote_v0332.ps1")
    assert "art-reference" not in spec
    assert draft_name not in theme
    assert f"art-reference\\generated-v0.3\\{draft_name}" in wrapper
    assert f"themes\\first-encounter\\assets\\{draft_name}" not in wrapper
