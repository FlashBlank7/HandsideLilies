from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_v0320_wrapper_remains_an_immutable_historical_release_contract() -> None:
    wrapper = _read("scripts/promote_v0320.ps1")

    assert "$ReleaseVersion = '0.3.20'" in wrapper
    assert "$FileVersion = '0.3.20.0'" in wrapper
    assert "packaged-self-test-v0320.json" in wrapper
    assert "packaged-compact-resource-v0320.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0320.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0320.json'" in wrapper
    assert "$ReleaseVersion = '0.3.21'" not in wrapper
    assert "packaged-self-test-v0321.json" not in wrapper
    assert "packaged-windows-startup-v0321.json" not in wrapper


def test_v0321_release_keeps_the_v0320_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0321.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0320.ps1'" in current_wrapper
    assert "'tests\\test_version_alignment_v0320.py'" in current_wrapper
    current_heading = (
        "### v0.3.21 Windows 启动恢复、桌面切换 ACK、响应式专注与新姿态"
    )
    historical_heading = "### v0.3.20 轻量盒核与发布边界更新"
    assert current_heading in readme
    assert historical_heading in readme
    assert readme.index(current_heading) < readme.index(historical_heading)
