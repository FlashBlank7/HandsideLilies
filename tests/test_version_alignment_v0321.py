from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_WRAPPER_SHA256 = (
    "97460f97cdcc5702cc05158621441371dc995010bb2abf425ded15f3e57c9fcc"
)


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_v0321_wrapper_remains_an_immutable_historical_release_contract() -> None:
    path = PROJECT_ROOT / "scripts" / "promote_v0321.ps1"
    wrapper = path.read_text(encoding="utf-8")

    assert hashlib.sha256(path.read_bytes()).hexdigest() == HISTORICAL_WRAPPER_SHA256
    assert "$ReleaseVersion = '0.3.21'" in wrapper
    assert "$FileVersion = '0.3.21.0'" in wrapper
    assert "packaged-self-test-v0321.json" in wrapper
    assert "packaged-compact-resource-v0321.json" in wrapper
    assert "packaged-windows-startup-v0321.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0321.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0321.json'" in wrapper
    assert "$ReleaseVersion = '0.3.22'" not in wrapper
    assert "packaged-self-test-v0322.json" not in wrapper


def test_v0322_release_keeps_the_v0321_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0322.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0321.ps1'" in current_wrapper
    assert "'tests\\test_version_alignment_v0321.py'" in current_wrapper
    current_heading = "### v0.3.22 主动陪伴可见性与订阅协议兼容更新"
    historical_heading = (
        "### v0.3.21 Windows 启动恢复、桌面切换 ACK、响应式专注与新姿态"
    )
    assert current_heading in readme
    assert historical_heading in readme
    assert readme.index(current_heading) < readme.index(historical_heading)
