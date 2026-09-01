from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_WRAPPER_SHA256 = (
    "46b85a8d59549d1ebfd860f6947a0cba4dcf0eec2a88691e6cb7f174a57fd0e9"
)


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_v0326_wrapper_remains_an_immutable_historical_release_contract() -> None:
    path = PROJECT_ROOT / "scripts" / "promote_v0326.ps1"
    wrapper = path.read_text(encoding="utf-8")

    assert hashlib.sha256(path.read_bytes()).hexdigest() == HISTORICAL_WRAPPER_SHA256
    assert "$ReleaseVersion = '0.3.26'" in wrapper
    assert "$FileVersion = '0.3.26.0'" in wrapper
    assert "packaged-self-test-v0326.json" in wrapper
    assert "packaged-compact-resource-v0326.json" in wrapper
    assert "packaged-windows-startup-v0326.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0326.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0326.json'" in wrapper
    assert "$ReleaseVersion = '0.3.27'" not in wrapper
    assert "packaged-self-test-v0327.json" not in wrapper


def test_v0327_release_keeps_the_v0326_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0327.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0326.ps1'" in current_wrapper
    assert "'tests\\test_version_alignment_v0326.py'" in current_wrapper
    current_heading = "### v0.3.27"
    historical_heading = "### v0.3.26"
    assert current_heading in readme
    assert historical_heading in readme
    assert readme.index(current_heading) < readme.index(historical_heading)
