from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.3.43"
HISTORICAL_V0338_SHA256 = (
    "71222a2c88b4d54578e03cffd3298f3ddb8d3c3e064c2b29c97d69aa1b790692"
)
HISTORICAL_V0339_SHA256 = (
    "ebb02dd3b850823a724499a4f3011fcdaa9ef2b015ea9b367fb01454f5a605d7"
)
HISTORICAL_V0340_SHA256 = (
    "97fb51e1164410eacabb02b463f6f8d1f49b614eb02b6afdb3b6853a65d3088c"
)
HISTORICAL_V0341_SHA256 = (
    "409d0e8ecccf0e32d1559edd4d015ff7b5fb2e143c76cf5d4a26c0d2cf4eb718"
)
HISTORICAL_V0342_SHA256 = (
    "f024f3cc081ab889cdd8a7d08841450a035707767a4dc34b6867f5b08353c758"
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


def test_v0343_active_version_points_are_aligned() -> None:
    markers = {
        "pyproject.toml": 'version = "0.3.43"',
        "uv.lock": 'version = "0.3.43"',
        "src/lilies_in_the_box.egg-info/PKG-INFO": "Version: 0.3.43",
        "src/lilies/__init__.py": '__version__ = "0.3.43"',
        "src/lilies/app.py": 'app.setApplicationVersion("0.3.43")',
        "src/lilies/core/codex_subscription.py": 'CLIENT_VERSION = "0.3.43"',
        "src/lilies/connectors/slack_websocket.py": (
            '_USER_AGENT = "lilies-in-the-box/0.3.43"'
        ),
        "scripts/install_windows.ps1": "version = '0.3.43'",
    }
    for relative, marker in markers.items():
        assert marker in _read(relative), relative

    theme = json.loads(_read("themes/first-encounter/theme.json"))
    assert theme["version"] == RELEASE_VERSION

    version_info = _read("packaging/windows_version_info.txt")
    for marker in ("(0, 3, 43, 0)", '"0.3.43.0"', '"0.3.43"'):
        assert marker in version_info


def test_v0343_report_build_install_and_verify_pointers_are_aligned() -> None:
    pointers = {
        "scripts/build_windows.ps1": "packaged-self-test-v0343.json",
        "scripts/verify_codex_subscription_smoke.py": ".codex-subscription-smoke-v0343",
        "scripts/verify_packaged_windows_startup.ps1": "packaged-windows-startup-v0343.json",
        "scripts/verify_packaged_compact_resources.ps1": "packaged-compact-resource-v0343.json",
        "scripts/verify_pose_click_masks.py": "pose-click-mask-v0343.json",
        "tests/test_pose_click_mask_qml_v0329.py": "pose-click-mask-v0343.json",
        "tests/test_windows_startup_probe_contract.py": "packaged-windows-startup-v0343.json",
    }
    for relative, marker in pointers.items():
        source = _read(relative)
        assert marker in source, relative

    active_sources = {
        "scripts/build_windows.ps1",
        "scripts/verify_codex_subscription_smoke.py",
        "scripts/verify_packaged_windows_startup.ps1",
        "scripts/verify_packaged_compact_resources.ps1",
        "scripts/verify_pose_click_masks.py",
        "tests/test_pose_click_mask_qml_v0329.py",
    }
    for relative in active_sources:
        for historical_marker in ("v0338", "v0339", "v0340", "v0341"):
            assert historical_marker not in _read(relative), relative

    startup_contract = _read("tests/test_windows_startup_probe_contract.py")
    assert 'assert "packaged-windows-startup-v0338.json" in probe' not in startup_contract
    assert 'assert "packaged-windows-startup-v0340.json" in probe' not in startup_contract
    assert 'assert "packaged-windows-startup-v0341.json" in probe' not in startup_contract

    build = _read("scripts/build_windows.ps1")
    for report in (
        "packaged-self-test-v0343.json",
        "packaged-compact-resource-v0343.json",
        "packaged-windows-startup-v0343.json",
        "pose-click-mask-v0343.json",
    ):
        assert report in build
    assert "Built Lilies v0.3.43" in build

    compact = _read("scripts/verify_packaged_compact_resources.ps1")
    assert "$ExpectedApplicationVersion = '0.3.43'" in compact
    assert "packaged-self-test-v0343.json" in compact

    installer = _read("scripts/install_windows.ps1")
    assert "$ExpectedFileVersion = '0.3.43.0'" in installer
    assert "$ExpectedProductVersion = '0.3.43'" in installer


def test_v0343_release_evidence_is_bound_to_the_packaged_candidate() -> None:
    verifier = _read("scripts/verify_pose_click_masks.py")
    for argument in ("--executable", "--report-path", "--resource-root"):
        assert argument in verifier
    for identity_field in (
        '"schemaVersion": 1',
        '"applicationVersion": application_version',
        '"executableSha256": hashlib.sha256(',
        '"capturedAt": datetime.now(UTC).isoformat()',
    ):
        assert identity_field in verifier
    assert "executable.read_bytes()" in verifier
    assert 'resource_root / "qml"' in verifier
    assert 'resource_root / "themes"' in verifier

    build = _read("scripts/build_windows.ps1")
    pose_invocation = build[build.index("verify_pose_click_masks.py") :]
    assert "--executable $CandidateExe" in pose_invocation
    assert "--report-path $PoseClickMask" in pose_invocation
    assert "--resource-root $InternalRoot" in pose_invocation

    wrapper = _read("scripts/promote_v0343.ps1")
    assert "Get-RequiredJsonInteger $selfTest 'schemaVersion' 'selfTest'" in wrapper
    assert "Assert-JsonBoolean $selfTest 'passed' $true 'selfTest'" in wrapper
    assert "Get-RequiredJsonInteger $Report 'schemaVersion' 'poseClickMask'" in wrapper
    assert (
        "Assert-JsonString $Report 'applicationVersion' "
        "$ExpectedApplicationVersion 'poseClickMask'"
    ) in wrapper
    assert (
        "Assert-JsonString $Report 'executableSha256' "
        "$ExpectedExecutableSha256 'poseClickMask'"
    ) in wrapper
    assert "packaged _internal directory" in wrapper
    assert "$windowsStartup, $poseClickMask" in wrapper


def test_v0343_wrapper_fences_all_immutable_historical_wrappers() -> None:
    historical_v0338 = PROJECT_ROOT / "scripts" / "promote_v0338.ps1"
    historical_v0339 = PROJECT_ROOT / "scripts" / "promote_v0339.ps1"
    historical_v0340 = PROJECT_ROOT / "scripts" / "promote_v0340.ps1"
    historical_v0341 = PROJECT_ROOT / "scripts" / "promote_v0341.ps1"
    historical_v0342 = PROJECT_ROOT / "scripts" / "promote_v0342.ps1"
    wrapper = _read("scripts/promote_v0343.ps1")

    assert (
        hashlib.sha256(historical_v0338.read_bytes()).hexdigest()
        == HISTORICAL_V0338_SHA256
    )
    assert (
        hashlib.sha256(historical_v0339.read_bytes()).hexdigest()
        == HISTORICAL_V0339_SHA256
    )
    assert (
        hashlib.sha256(historical_v0340.read_bytes()).hexdigest()
        == HISTORICAL_V0340_SHA256
    )
    assert (
        hashlib.sha256(historical_v0341.read_bytes()).hexdigest()
        == HISTORICAL_V0341_SHA256
    )
    assert (
        hashlib.sha256(historical_v0342.read_bytes()).hexdigest()
        == HISTORICAL_V0342_SHA256
    )
    assert HISTORICAL_V0338_SHA256 in wrapper
    assert HISTORICAL_V0339_SHA256 in wrapper
    assert HISTORICAL_V0340_SHA256 in wrapper
    assert HISTORICAL_V0341_SHA256 in wrapper
    assert HISTORICAL_V0342_SHA256 in wrapper
    assert "$HistoricalV0338" in wrapper
    assert "$HistoricalV0339" in wrapper
    assert "$HistoricalV0340" in wrapper
    assert "$HistoricalV0341" in wrapper
    assert "$HistoricalV0342" in wrapper
    assert "$ReleaseVersion = '0.3.43'" in wrapper
    assert "$FileVersion = '0.3.43.0'" in wrapper
    assert "packaged-self-test-v0343.json" in wrapper
    assert "packaged-compact-resource-v0343.json" in wrapper
    assert "packaged-windows-startup-v0343.json" in wrapper
    assert "pose-click-mask-v0343.json" in wrapper
    assert r"-PromotionScript 'scripts\promote_v0343.ps1'" in wrapper
    assert r"-PackagedReport 'artifacts\packaged-self-test-v0343.json'" in wrapper


def test_v0343_additional_files_and_checksum_fence_cover_release_delta() -> None:
    additional, fence = _release_file_sets(_read("scripts/promote_v0343.ps1"))
    required = {
        ".gitignore",
        "LiliesInTheBox.spec",
        "README.md",
        r"packaging\hooks\hook-PySide6.QtQml.py",
        r"packaging\windows_version_info.txt",
        "pyproject.toml",
        "uv.lock",
        r"qml\Main.qml",
        r"qml\CompanionBubble.qml",
        r"qml\V03PetBody.qml",
        r"scripts\build_windows.ps1",
        r"scripts\install_windows.ps1",
        r"scripts\restore_windows.ps1",
        r"scripts\setup_windows.ps1",
        r"scripts\promote_v0336.ps1",
        r"scripts\promote_v0337.ps1",
        r"scripts\promote_v0338.ps1",
        r"scripts\promote_v0339.ps1",
        r"scripts\promote_v0340.ps1",
        r"scripts\promote_v0341.ps1",
        r"scripts\promote_v0342.ps1",
        r"scripts\promote_v0343.ps1",
        r"scripts\verify_codex_subscription_smoke.py",
        r"scripts\verify_companion_bubbles.py",
        r"scripts\verify_companion_bubble_matrix.py",
        r"scripts\verify_companion_frequency_draft.py",
        r"scripts\verify_companion_presentation_gate.py",
        r"scripts\verify_drag_runtime_v0325.py",
        r"scripts\verify_packaged_compact_resources.ps1",
        r"scripts\verify_packaged_windows_startup.ps1",
        r"scripts\verify_pose_click_masks.py",
        r"src\lilies\__init__.py",
        r"src\lilies\app.py",
        r"src\lilies\backend.py",
        r"src\lilies\companion_controller.py",
        r"src\lilies\connectors\slack_socket.py",
        r"src\lilies\connectors\slack_websocket.py",
        r"src\lilies\core\codex_subscription.py",
        r"src\lilies\core\companion.py",
        r"src\lilies\core\companion_delivery.py",
        r"src\lilies\core\companion_runtime.py",
        r"src\lilies\core\database.py",
        r"src\lilies\core\data_migration.py",
        r"src\lilies\core\model.py",
        r"src\lilies_in_the_box.egg-info\PKG-INFO",
        r"tests\conftest.py",
        r"tests\test_atomic_companion_frequency.py",
        r"tests\test_backend_v03_contract.py",
        r"tests\test_compact_hit_test.py",
        r"tests\test_companion.py",
        r"tests\test_companion_bubbles_offscreen.py",
        r"tests\test_companion_controller.py",
        r"tests\test_companion_discoverability_qml.py",
        r"tests\test_companion_frequency_draft_v0332.py",
        r"tests\test_companion_frequency_projection_hardening.py",
        r"tests\test_companion_persistence.py",
        r"tests\test_companion_preferences_signal.py",
        r"tests\test_companion_presentation_gate_qml.py",
        r"tests\test_data_migration.py",
        r"tests\test_drag_follow_contract_v0328.py",
        r"tests\test_native_drag_press_contract_v0320.py",
        r"tests\test_pose_click_mask_qml_v0329.py",
        r"tests\test_packaged_user_flow_self_test_contract.py",
        r"tests\test_packaging_qml_pruning_v0340.py",
        r"tests\test_idle_runtime_budget_v0340.py",
        r"tests\test_qml_warning_release_gate_v0340.py",
        r"tests\test_packaged_footprint_gate_v0340.py",
        r"tests\test_promotion_retry_contract.py",
        r"tests\test_slack_socket_v03.py",
        r"tests\test_slack_websocket_transport.py",
        r"tests\test_version_alignment_v0336.py",
        r"tests\test_version_alignment_v0337.py",
        r"tests\test_version_alignment_v0338.py",
        r"tests\test_version_alignment_v0339.py",
        r"tests\test_version_alignment_v0340.py",
        r"tests\test_windows_installer_contract.py",
        r"tests\test_windows_startup_probe_contract.py",
        r"tests\test_windows_version_resource_contract.py",
        r"themes\first-encounter\theme.json",
    }
    assert required <= additional
    assert required <= fence

    reports = {
        r"artifacts\packaged-self-test-v0343.json",
        r"artifacts\packaged-compact-resource-v0343.json",
        r"artifacts\packaged-windows-startup-v0343.json",
        r"artifacts\pose-click-mask-v0343.json",
    }
    assert reports <= additional
    assert reports <= fence


def test_v0343_readme_is_current_and_documents_release_gates() -> None:
    readme = _read("README.md")
    assert readme.index("### v0.3.43") < readme.index("### v0.3.42")
    current = readme[readme.index("### v0.3.43") : readme.index("### v0.3.42")]
    for marker in (
        "165Hz",
        "Windows 原生系统拖动",
        "FrameAnimation",
        "MouseMove",
        "冻结",
        "v0.3.43",
        "SHA-256",
        "v0.3.42",
    ):
        assert marker in current
    assert "promote_v0343.ps1 -ValidateOnly" in readme
