from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_WRAPPER_SHA256 = (
    "21ae43ded15a8a4e25a2f3569422e5cd40ea7ceb1ee0ede29913999d07da960a"
)


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_v0327_wrapper_remains_an_immutable_historical_release_contract() -> None:
    path = PROJECT_ROOT / "scripts" / "promote_v0327.ps1"
    wrapper = path.read_text(encoding="utf-8")

    assert hashlib.sha256(path.read_bytes()).hexdigest() == HISTORICAL_WRAPPER_SHA256
    assert "$ReleaseVersion = '0.3.27'" in wrapper
    assert "$FileVersion = '0.3.27.0'" in wrapper
    assert "packaged-self-test-v0327.json" in wrapper
    assert "packaged-compact-resource-v0327.json" in wrapper
    assert "packaged-windows-startup-v0327.json" in wrapper
    assert "-PromotionScript 'scripts\\promote_v0327.ps1'" in wrapper
    assert "-PackagedReport 'artifacts\\packaged-self-test-v0327.json'" in wrapper
    assert "$ReleaseVersion = '0.3.28'" not in wrapper
    assert "packaged-self-test-v0328.json" not in wrapper


def test_v0328_release_keeps_the_v0327_wrapper_and_history_entry() -> None:
    current_wrapper = _read("scripts/promote_v0328.ps1")
    readme = _read("README.md")

    assert "'scripts\\promote_v0327.ps1'" in current_wrapper
    assert "'tests\\test_version_alignment_v0327.py'" in current_wrapper
    current_heading = "### v0.3.28"
    historical_heading = "### v0.3.27"
    assert current_heading in readme
    assert historical_heading in readme
    assert readme.index(current_heading) < readme.index(historical_heading)
