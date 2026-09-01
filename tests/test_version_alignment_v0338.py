from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.3.38"
HISTORICAL_V0337_SHA256 = (
    "bc7c598c022be8c1d423718336466b5c126f063bff382ccdd1e292d1981b81de"
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


def _legacy_test_v0338_active_version_points_are_aligned() -> None:
    markers = {
        "pyproject.toml": 'version = "0.3.38"',
        "uv.lock": 'version = "0.3.38"',
        "src/lilies_in_the_box.egg-info/PKG-INFO": "Version: 0.3.38",
        "src/lilies/__init__.py": '__version__ = "0.3.38"',
        "src/lilies/app.py": 'app.setApplicationVersion("0.3.38")',
        "src/lilies/core/codex_subscription.py": 'CLIENT_VERSION = "0.3.38"',
        "scripts/install_windows.ps1": "version = '0.3.38'",
    }
    for relative, marker in markers.items():
        assert marker in _read(relative), relative

    theme = json.loads(_read("themes/first-encounter/theme.json"))
    assert theme["version"] == RELEASE_VERSION

    version_info = _read("packaging/windows_version_info.txt")
    for marker in ("(0, 3, 38, 0)", '"0.3.38.0"', '"0.3.38"'):
        assert marker in version_info


def _legacy_test_v0338_report_build_install_and_verify_pointers_are_aligned() -> None:
    pointers = {
        "scripts/build_windows.ps1": "packaged-self-test-v0338.json",
        "scripts/verify_codex_subscription_smoke.py": ".codex-subscription-smoke-v0338",
        "scripts/verify_packaged_windows_startup.ps1": "packaged-windows-startup-v0338.json",
        "scripts/verify_packaged_compact_resources.ps1": "packaged-compact-resource-v0338.json",
        "scripts/verify_pose_click_masks.py": "pose-click-mask-v0338.json",
        "tests/test_pose_click_mask_qml_v0329.py": "pose-click-mask-v0338.json",
        "tests/test_windows_startup_probe_contract.py": "packaged-windows-startup-v0338.json",
    }
    for relative, marker in pointers.items():
        source = _read(relative)
        assert marker in source, relative
        assert "v0337" not in source, relative

    build = _read("scripts/build_windows.ps1")
    for report in (
        "packaged-self-test-v0338.json",
        "packaged-compact-resource-v0338.json",
        "packaged-windows-startup-v0338.json",
        "pose-click-mask-v0338.json",
    ):
        assert report in build
    assert "Built Lilies v0.3.38" in build

    compact = _read("scripts/verify_packaged_compact_resources.ps1")
    assert "$ExpectedApplicationVersion = '0.3.38'" in compact
    assert "packaged-self-test-v0338.json" in compact

    installer = _read("scripts/install_windows.ps1")
    assert "$ExpectedFileVersion = '0.3.38.0'" in installer
    assert "$ExpectedProductVersion = '0.3.38'" in installer


def test_v0338_wrapper_fences_the_immutable_v0337_wrapper() -> None:
    historical = PROJECT_ROOT / "scripts" / "promote_v0337.ps1"
    wrapper = _read("scripts/promote_v0338.ps1")

    assert hashlib.sha256(historical.read_bytes()).hexdigest() == HISTORICAL_V0337_SHA256
    assert HISTORICAL_V0337_SHA256 in wrapper
    assert "$HistoricalV0337" in wrapper
    assert "$ReleaseVersion = '0.3.38'" in wrapper
    assert "$FileVersion = '0.3.38.0'" in wrapper
    assert "packaged-self-test-v0338.json" in wrapper
    assert "packaged-compact-resource-v0338.json" in wrapper
    assert "packaged-windows-startup-v0338.json" in wrapper
    assert "pose-click-mask-v0338.json" in wrapper
    assert r"-PromotionScript 'scripts\promote_v0338.ps1'" in wrapper
    assert r"-PackagedReport 'artifacts\packaged-self-test-v0338.json'" in wrapper


def test_v0338_additional_files_and_checksum_fence_cover_release_delta() -> None:
    additional, fence = _release_file_sets(_read("scripts/promote_v0338.ps1"))
    required = {
        "README.md",
        r"packaging\windows_version_info.txt",
        "pyproject.toml",
        "uv.lock",
        r"qml\Main.qml",
        r"scripts\build_windows.ps1",
        r"scripts\install_windows.ps1",
        r"scripts\promote_v0336.ps1",
        r"scripts\promote_v0337.ps1",
        r"scripts\promote_v0338.ps1",
        r"scripts\verify_codex_subscription_smoke.py",
        r"scripts\verify_companion_frequency_draft.py",
        r"scripts\verify_companion_presentation_gate.py",
        r"scripts\verify_packaged_compact_resources.ps1",
        r"scripts\verify_packaged_windows_startup.ps1",
        r"scripts\verify_pose_click_masks.py",
        r"src\lilies\__init__.py",
        r"src\lilies\app.py",
        r"src\lilies\companion_controller.py",
        r"src\lilies\core\codex_subscription.py",
        r"src\lilies\core\database.py",
        r"src\lilies_in_the_box.egg-info\PKG-INFO",
        r"tests\conftest.py",
        r"tests\test_atomic_companion_frequency.py",
        r"tests\test_companion_controller.py",
        r"tests\test_companion_frequency_draft_v0332.py",
        r"tests\test_companion_frequency_projection_hardening.py",
        r"tests\test_companion_preferences_signal.py",
        r"tests\test_companion_presentation_gate_qml.py",
        r"tests\test_pose_click_mask_qml_v0329.py",
        r"tests\test_promotion_retry_contract.py",
        r"tests\test_version_alignment_v0336.py",
        r"tests\test_version_alignment_v0337.py",
        r"tests\test_version_alignment_v0338.py",
        r"tests\test_windows_installer_contract.py",
        r"tests\test_windows_startup_probe_contract.py",
        r"tests\test_windows_version_resource_contract.py",
        r"themes\first-encounter\theme.json",
    }
    assert required <= additional
    assert required <= fence

    reports = {
        r"artifacts\packaged-self-test-v0338.json",
        r"artifacts\packaged-compact-resource-v0338.json",
        r"artifacts\packaged-windows-startup-v0338.json",
        r"artifacts\pose-click-mask-v0338.json",
    }
    assert reports <= additional
    assert reports <= fence


def _legacy_test_v0338_readme_is_current_and_documents_static_promotion() -> None:
    readme = _read("README.md")
    assert readme.index("### v0.3.38") < readme.index("### v0.3.37")
    current = readme[readme.index("### v0.3.38") : readme.index("### v0.3.37")]
    for marker in ("不会静默覆盖", "仍然应用", "所有者线程", "v0.3.38", "SHA-256", "v0.3.37"):
        assert marker in current
    assert "promote_v0338.ps1 -ValidateOnly" in readme
