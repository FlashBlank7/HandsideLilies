from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_v0318_wrapper_remains_an_immutable_historical_release_contract() -> None:
    wrapper = _read("scripts/promote_v0318.ps1")

    assert "$ReleaseVersion = '0.3.18'" in wrapper
    assert "$FileVersion = '0.3.18.0'" in wrapper
    assert "packaged-self-test-v0318.json" in wrapper
    assert "packaged-compact-resource-v0318.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0318.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0318.json'" in wrapper
    assert "$ReleaseVersion = '0.3.19'" not in wrapper
    assert "packaged-self-test-v0319.json" not in wrapper
    assert "$ReleaseVersion = '0.3.20'" not in wrapper
    assert "packaged-self-test-v0320.json" not in wrapper


def test_v0320_release_keeps_the_v0318_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0320.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0318.ps1'" in current_wrapper
    assert "'scripts\\promote_v0319.ps1'" in current_wrapper
    assert "### v0.3.18 桌面在场与发布可靠性更新" in readme
