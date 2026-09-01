from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.3.35"
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


def _legacy_v0335_active_version_points_are_aligned() -> None:
    markers = {
        "pyproject.toml": 'version = "0.3.35"',
        "uv.lock": 'version = "0.3.35"',
        "src/lilies_in_the_box.egg-info/PKG-INFO": "Version: 0.3.35",
        "src/lilies/__init__.py": '__version__ = "0.3.35"',
        "src/lilies/app.py": 'app.setApplicationVersion("0.3.35")',
        "src/lilies/core/codex_subscription.py": 'CLIENT_VERSION = "0.3.35"',
        "scripts/install_windows.ps1": "version = '0.3.35'",
    }
    for relative, marker in markers.items():
        assert marker in _read(relative), relative

    theme = json.loads(_read("themes/first-encounter/theme.json"))
    assert theme["version"] == RELEASE_VERSION

    version_info = _read("packaging/windows_version_info.txt")
    for marker in ("(0, 3, 35, 0)", '"0.3.35.0"', '"0.3.35"'):
        assert marker in version_info


def _legacy_v0335_report_build_install_and_verify_pointers_are_aligned() -> None:
    pointers = {
        "scripts/build_windows.ps1": "packaged-self-test-v0335.json",
        "scripts/verify_codex_subscription_smoke.py": ".codex-subscription-smoke-v0335",
        "scripts/verify_packaged_windows_startup.ps1": "packaged-windows-startup-v0335.json",
        "scripts/verify_packaged_compact_resources.ps1": "packaged-compact-resource-v0335.json",
        "scripts/verify_pose_click_masks.py": "pose-click-mask-v0335.json",
        "tests/test_pose_click_mask_qml_v0329.py": "pose-click-mask-v0335.json",
        "tests/test_windows_startup_probe_contract.py": "packaged-windows-startup-v0335.json",
    }
    for relative, marker in pointers.items():
        assert marker in _read(relative), relative

    build = _read("scripts/build_windows.ps1")
    for report in (
        "packaged-self-test-v0335.json",
        "packaged-compact-resource-v0335.json",
        "packaged-windows-startup-v0335.json",
        "pose-click-mask-v0335.json",
    ):
        assert report in build
    assert "Built Lilies v0.3.35" in build

    compact = _read("scripts/verify_packaged_compact_resources.ps1")
    assert "$ExpectedApplicationVersion = '0.3.35'" in compact
    assert "packaged-self-test-v0335.json" in compact

    installer = _read("scripts/install_windows.ps1")
    assert "$ExpectedFileVersion = '0.3.35.0'" in installer
    assert "$ExpectedProductVersion = '0.3.35'" in installer


def test_v0335_wrapper_fences_the_immutable_v0334_wrapper() -> None:
    historical = PROJECT_ROOT / "scripts" / "promote_v0334.ps1"
    wrapper = _read("scripts/promote_v0335.ps1")

    assert hashlib.sha256(historical.read_bytes()).hexdigest() == HISTORICAL_V0334_SHA256
    assert HISTORICAL_V0334_SHA256 in wrapper
    assert "$HistoricalV0334" in wrapper
    assert "$ReleaseVersion = '0.3.35'" in wrapper
    assert "$FileVersion = '0.3.35.0'" in wrapper
    assert "packaged-self-test-v0335.json" in wrapper
    assert "packaged-compact-resource-v0335.json" in wrapper
    assert "packaged-windows-startup-v0335.json" in wrapper
    assert "pose-click-mask-v0335.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0335.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0335.json'" in wrapper


def test_v0335_additional_files_and_checksum_fence_cover_release_delta() -> None:
    additional, fence = _release_file_sets(_read("scripts/promote_v0335.ps1"))
    required = {
        "README.md",
        "packaging\\windows_version_info.txt",
        "pyproject.toml",
        "qml\\Main.qml",
        "qml\\V03Dock.qml",
        "qml\\V03WorkPanel.qml",
        "scripts\\build_windows.ps1",
        "scripts\\install_windows.ps1",
        "scripts\\promote_v0334.ps1",
        "scripts\\promote_v0335.ps1",
        "scripts\\verify_box_world_click_path.py",
        "scripts\\verify_codex_subscription_smoke.py",
        "scripts\\verify_dock_ui.py",
        "scripts\\verify_entry_actions.py",
        "scripts\\verify_packaged_compact_resources.ps1",
        "scripts\\verify_packaged_windows_startup.ps1",
        "scripts\\verify_pose_click_masks.py",
        "src\\lilies\\__init__.py",
        "src\\lilies\\app.py",
        "src\\lilies\\backend.py",
        "src\\lilies\\companion_controller.py",
        "src\\lilies\\core\\activity.py",
        "src\\lilies\\core\\codex_subscription.py",
        "src\\lilies\\core\\companion_delivery.py",
        "src\\lilies\\core\\companion_runtime.py",
        "src\\lilies\\core\\shell.py",
        "src\\lilies\\core\\socket_server.py",
        "src\\lilies_in_the_box.egg-info\\PKG-INFO",
        "tests\\test_activity_context.py",
        "tests\\test_backend_v03_contract.py",
        "tests\\test_box_world_click_path_offscreen.py",
        "tests\\test_companion.py",
        "tests\\test_companion_controller.py",
        "tests\\test_companion_discoverability_qml.py",
        "tests\\test_companion_resilience_v0333.py",
        "tests\\test_dock_ui_offscreen.py",
        "tests\\test_entry_actions_offscreen.py",
        "tests\\test_pose_click_mask_qml_v0329.py",
        "tests\\test_promotion_retry_contract.py",
        "tests\\test_runtime_recovery_hardening_v0335.py",
        "tests\\test_runtime_snapshot_socket.py",
        "tests\\test_shell_recovery.py",
        "tests\\test_version_alignment_v0334.py",
        "tests\\test_version_alignment_v0335.py",
        "tests\\test_windows_installer_contract.py",
        "tests\\test_windows_startup_probe_contract.py",
        "tests\\test_windows_version_resource_contract.py",
        "themes\\first-encounter\\theme.json",
        "uv.lock",
    }
    assert required <= additional
    assert required <= fence

    reports = {
        "artifacts\\packaged-self-test-v0335.json",
        "artifacts\\packaged-compact-resource-v0335.json",
        "artifacts\\packaged-windows-startup-v0335.json",
        "artifacts\\pose-click-mask-v0335.json",
    }
    assert reports <= additional
    assert reports <= fence


def _legacy_v0335_readme_is_current_and_documents_static_promotion() -> None:
    readme = _read("README.md")
    assert readme.index("### v0.3.35") < readme.index("### v0.3.34")
    current = readme[readme.index("### v0.3.35") : readme.index("### v0.3.34")]
    for marker in ("Qt 主线程心跳", "v0.3.35", "SHA-256", "v0.3.34"):
        assert marker in current
    assert "promote_v0335.ps1 -ValidateOnly" in readme


def _legacy_v0335_browser_pixel_pause_text_is_current() -> None:
    controller = _read("src/lilies/companion_controller.py")
    readme = _read("README.md")
    assert "浏览器像素观察在 v0.3.35 暂不开放" in controller
    assert "浏览器像素观察在 v0.3.34 暂不开放" not in controller
    assert "浏览器像素观察在 v0.3.35 继续暂停" in readme
