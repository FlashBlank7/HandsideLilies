from __future__ import annotations

import hashlib
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_V0338_SHA256 = (
    "71222a2c88b4d54578e03cffd3298f3ddb8d3c3e064c2b29c97d69aa1b790692"
)
HISTORICAL_V0339_SHA256 = (
    "ebb02dd3b850823a724499a4f3011fcdaa9ef2b015ea9b367fb01454f5a605d7"
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


def test_v0339_wrapper_preserves_its_release_identity() -> None:
    wrapper = _read("scripts/promote_v0339.ps1")

    assert "$ReleaseVersion = '0.3.39'" in wrapper
    assert "$FileVersion = '0.3.39.0'" in wrapper
    assert r"-PromotionScript 'scripts\promote_v0339.ps1'" in wrapper
    assert r"-PackagedReport 'artifacts\packaged-self-test-v0339.json'" in wrapper


def test_v0339_historical_evidence_names_remain_self_consistent() -> None:
    wrapper = _read("scripts/promote_v0339.ps1")
    for report in (
        "packaged-self-test-v0339.json",
        "packaged-compact-resource-v0339.json",
        "packaged-windows-startup-v0339.json",
        "pose-click-mask-v0339.json",
    ):
        assert report in wrapper
    assert "v0.3.39 release gate" in wrapper


def test_v0339_release_evidence_is_bound_to_the_packaged_candidate() -> None:
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

    wrapper = _read("scripts/promote_v0339.ps1")
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


def test_v0339_wrapper_fences_the_immutable_v0338_wrapper() -> None:
    historical = PROJECT_ROOT / "scripts" / "promote_v0338.ps1"
    historical_v0339 = PROJECT_ROOT / "scripts" / "promote_v0339.ps1"
    wrapper = _read("scripts/promote_v0339.ps1")

    assert hashlib.sha256(historical.read_bytes()).hexdigest() == HISTORICAL_V0338_SHA256
    assert (
        hashlib.sha256(historical_v0339.read_bytes()).hexdigest()
        == HISTORICAL_V0339_SHA256
    )
    assert HISTORICAL_V0338_SHA256 in wrapper
    assert "$HistoricalV0338" in wrapper
    assert "$ReleaseVersion = '0.3.39'" in wrapper
    assert "$FileVersion = '0.3.39.0'" in wrapper
    assert "packaged-self-test-v0339.json" in wrapper
    assert "packaged-compact-resource-v0339.json" in wrapper
    assert "packaged-windows-startup-v0339.json" in wrapper
    assert "pose-click-mask-v0339.json" in wrapper
    assert r"-PromotionScript 'scripts\promote_v0339.ps1'" in wrapper
    assert r"-PackagedReport 'artifacts\packaged-self-test-v0339.json'" in wrapper


def test_v0339_additional_files_and_checksum_fence_cover_release_delta() -> None:
    additional, fence = _release_file_sets(_read("scripts/promote_v0339.ps1"))
    required = {
        "README.md",
        r"packaging\windows_version_info.txt",
        "pyproject.toml",
        "uv.lock",
        r"qml\Main.qml",
        r"qml\CompanionBubble.qml",
        r"scripts\build_windows.ps1",
        r"scripts\install_windows.ps1",
        r"scripts\promote_v0336.ps1",
        r"scripts\promote_v0337.ps1",
        r"scripts\promote_v0338.ps1",
        r"scripts\promote_v0339.ps1",
        r"scripts\verify_codex_subscription_smoke.py",
        r"scripts\verify_companion_bubbles.py",
        r"scripts\verify_companion_bubble_matrix.py",
        r"scripts\verify_companion_frequency_draft.py",
        r"scripts\verify_companion_presentation_gate.py",
        r"scripts\verify_packaged_compact_resources.ps1",
        r"scripts\verify_packaged_windows_startup.ps1",
        r"scripts\verify_pose_click_masks.py",
        r"src\lilies\__init__.py",
        r"src\lilies\app.py",
        r"src\lilies\companion_controller.py",
        r"src\lilies\connectors\slack_socket.py",
        r"src\lilies\connectors\slack_websocket.py",
        r"src\lilies\core\codex_subscription.py",
        r"src\lilies\core\companion.py",
        r"src\lilies\core\companion_delivery.py",
        r"src\lilies\core\companion_runtime.py",
        r"src\lilies\core\database.py",
        r"src\lilies\core\data_migration.py",
        r"src\lilies_in_the_box.egg-info\PKG-INFO",
        r"tests\conftest.py",
        r"tests\test_atomic_companion_frequency.py",
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
        r"tests\test_pose_click_mask_qml_v0329.py",
        r"tests\test_promotion_retry_contract.py",
        r"tests\test_slack_socket_v03.py",
        r"tests\test_slack_websocket_transport.py",
        r"tests\test_version_alignment_v0336.py",
        r"tests\test_version_alignment_v0337.py",
        r"tests\test_version_alignment_v0338.py",
        r"tests\test_version_alignment_v0339.py",
        r"tests\test_windows_installer_contract.py",
        r"tests\test_windows_startup_probe_contract.py",
        r"tests\test_windows_version_resource_contract.py",
        r"themes\first-encounter\theme.json",
    }
    assert required <= additional
    assert required <= fence

    reports = {
        r"artifacts\packaged-self-test-v0339.json",
        r"artifacts\packaged-compact-resource-v0339.json",
        r"artifacts\packaged-windows-startup-v0339.json",
        r"artifacts\pose-click-mask-v0339.json",
    }
    assert reports <= additional
    assert reports <= fence


def test_v0339_readme_is_current_and_documents_observation_contract() -> None:
    readme = _read("README.md")
    assert readme.index("### v0.3.40") < readme.index("### v0.3.39")
    assert readme.index("### v0.3.39") < readme.index("### v0.3.38")
    current = readme[readme.index("### v0.3.39") : readme.index("### v0.3.38")]
    for marker in (
        "观察当前窗口一次",
        "哲思",
        "摘要 + 详情",
        "去重",
        "失败",
        "绝不用泛化台词冒充",
        "v0.3.39",
        "SHA-256",
        "v0.3.38",
    ):
        assert marker in current
    assert "promote_v0342.ps1 -ValidateOnly" in readme
