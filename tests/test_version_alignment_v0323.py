from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_WRAPPER_SHA256 = (
    "f3fd12bb440ab1dd5bc3b999a8c3ffab6a872c181ac610032351e3d4102b520c"
)


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_v0323_wrapper_remains_an_immutable_historical_release_contract() -> None:
    path = PROJECT_ROOT / "scripts" / "promote_v0323.ps1"
    wrapper = path.read_text(encoding="utf-8")

    assert hashlib.sha256(path.read_bytes()).hexdigest() == HISTORICAL_WRAPPER_SHA256
    assert "$ReleaseVersion = '0.3.23'" in wrapper
    assert "$FileVersion = '0.3.23.0'" in wrapper
    assert "packaged-self-test-v0323.json" in wrapper
    assert "packaged-compact-resource-v0323.json" in wrapper
    assert "packaged-windows-startup-v0323.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0323.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0323.json'" in wrapper
    assert "$ReleaseVersion = '0.3.24'" not in wrapper
    assert "packaged-self-test-v0324.json" not in wrapper


def test_v0324_release_keeps_the_v0323_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0324.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0323.ps1'" in current_wrapper
    assert "'tests\\test_version_alignment_v0323.py'" in current_wrapper
    current_heading = "### v0.3.24 静默状态、盒中世界与窗口恢复更新"
    historical_heading = "### v0.3.23 桌面发现、专注反馈与姿态覆盖更新"
    assert current_heading in readme
    assert historical_heading in readme
    assert readme.index(current_heading) < readme.index(historical_heading)
