from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_WRAPPER_SHA256 = (
    "b7a51900c268adace6c8d1cef07b5cc674f31e3502bbfb865c52cd67111cfb56"
)


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_v0322_wrapper_remains_an_immutable_historical_release_contract() -> None:
    path = PROJECT_ROOT / "scripts" / "promote_v0322.ps1"
    wrapper = path.read_text(encoding="utf-8")

    assert hashlib.sha256(path.read_bytes()).hexdigest() == HISTORICAL_WRAPPER_SHA256
    assert "$ReleaseVersion = '0.3.22'" in wrapper
    assert "$FileVersion = '0.3.22.0'" in wrapper
    assert "packaged-self-test-v0322.json" in wrapper
    assert "packaged-compact-resource-v0322.json" in wrapper
    assert "packaged-windows-startup-v0322.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0322.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0322.json'" in wrapper
    assert "$ReleaseVersion = '0.3.23'" not in wrapper
    assert "packaged-self-test-v0323.json" not in wrapper


def test_v0323_release_keeps_the_v0322_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0323.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0322.ps1'" in current_wrapper
    assert "'tests\\test_version_alignment_v0322.py'" in current_wrapper
    current_heading = "### v0.3.23 桌面发现、专注反馈与姿态覆盖更新"
    historical_heading = "### v0.3.22 主动陪伴可见性与订阅协议兼容更新"
    assert current_heading in readme
    assert historical_heading in readme
    assert readme.index(current_heading) < readme.index(historical_heading)
