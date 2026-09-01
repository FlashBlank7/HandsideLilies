from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_WRAPPER_SHA256 = (
    "8dddd05c4b5b97e3d589853785323b5e1426d974389ef492601e56f560f8feed"
)


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_v0331_wrapper_remains_an_immutable_historical_release_contract() -> None:
    path = PROJECT_ROOT / "scripts" / "promote_v0331.ps1"
    wrapper = path.read_text(encoding="utf-8")

    assert hashlib.sha256(path.read_bytes()).hexdigest() == HISTORICAL_WRAPPER_SHA256
    assert "$ReleaseVersion = '0.3.31'" in wrapper
    assert "$FileVersion = '0.3.31.0'" in wrapper
    assert "packaged-self-test-v0331.json" in wrapper
    assert "packaged-compact-resource-v0331.json" in wrapper
    assert "packaged-windows-startup-v0331.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0331.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0331.json'" in wrapper
    assert "$ReleaseVersion = '0.3.32'" not in wrapper
    assert "packaged-self-test-v0332.json" not in wrapper


def test_v0332_release_keeps_the_v0331_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0332.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0331.ps1'" in current_wrapper
    assert "'tests\\test_version_alignment_v0331.py'" in current_wrapper
    assert "'tests\\test_release_focus_gate_v0331.py'" in current_wrapper
    assert "'tests\\test_release_qt_cache_gate_v0331.py'" in current_wrapper
    current_heading = "### v0.3.32"
    historical_heading = "### v0.3.31"
    assert current_heading in readme
    assert historical_heading in readme
    assert readme.index(current_heading) < readme.index(historical_heading)
