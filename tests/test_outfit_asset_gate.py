from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scripts.verify_outfit_assets import validate_outfit_assets


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEME_PATH = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
QML_PATH = PROJECT_ROOT / "qml" / "V03PetBody.qml"


def _error_text(report: dict[str, object]) -> str:
    return "\n".join(str(value) for value in report["errors"])


@pytest.fixture(scope="module")
def cloned_outfit_gate_inputs() -> tuple[Path, Path]:
    """Clone metadata and link reviewed masters without rewriting pixels."""

    source_root = THEME_PATH.parent
    scratch_root = PROJECT_ROOT / "artifacts"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="outfit-gate-", dir=scratch_root) as scratch:
        target_root = Path(scratch) / "first-encounter"
        target_root.mkdir(parents=True)
        manifest = json.loads(THEME_PATH.read_text(encoding="utf-8"))
        character = dict(manifest["character"])
        bundle_asset_keys = {
            str(dict(bundle)["asset"])
            for bundle in dict(character["outfitBundles"]).values()
        }
        for asset_key in bundle_asset_keys:
            relative = Path(str(manifest["assets"][asset_key]))
            source = source_root / relative
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                continue
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        manifest_path = target_root / "theme.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        qml_path = target_root / "V03PetBody.qml"
        shutil.copy2(QML_PATH, qml_path)
        yield manifest_path, qml_path


def test_first_encounter_formal_outfits_pass_the_gate() -> None:
    report = validate_outfit_assets(THEME_PATH, QML_PATH)

    assert report["passed"] is True, _error_text(report)
    assert report["anchorVersion"] == 2
    assert report["defaultOutfit"] == "first-encounter"
    assert len(report["outfits"]) == 6
    assert len(report["hashGroups"]) == 5
    assert report["outfits"]["summer-cotton-dress"]["implementationStatus"] == (
        "visual-alias"
    )
    assert report["outfits"]["summer-cotton-dress"]["visualAliasOf"] == (
        "first-encounter"
    )
    for outfit in report["outfits"].values():
        assert outfit["format"] == "PNG"
        assert outfit["resolutionTier"] == "production-v1"
        assert outfit["mode"] == "RGBA"
        assert outfit["bands"] == ["R", "G", "B", "A"]
        assert outfit["alphaExtrema"] == [0, 255]
        assert outfit["cornerTransparentRatios"] == [1.0, 1.0, 1.0, 1.0]
        assert outfit["transparentRgbLeakRatio"] == 0.0
        assert outfit["greenBoundaryRatio"] <= 0.002
        assert outfit["whiteFringeRiskRatio"] <= 0.01
        assert outfit["pixelSize"] == outfit["declaredPixelSize"]
        assert outfit["anchorVersion"] == 2
        # Layered outfits use V03PetBody's geometric silhouette at runtime;
        # unlike standalone pose artwork they do not declare a bitmap mask.
        assert outfit["clickMaskRelation"] is None


def test_gate_rejects_alias_anchor_ratio_and_slice_contract_drift(
    cloned_outfit_gate_inputs: tuple[Path, Path],
) -> None:
    manifest_path, qml_path = cloned_outfit_gate_inputs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundles = manifest["character"]["outfitBundles"]

    # A shared hash must be acknowledged as an alias, while a visual alias
    # must actually have the same reviewed bitmap as its canonical target.
    bundles["summer-cotton-dress"] = {
        "asset": "desktopPetSummer",
        "anchorVersion": 2,
        "implementationStatus": "production",
    }
    bundles["rest-nightdress"]["implementationStatus"] = "visual-alias"
    bundles["rest-nightdress"]["visualAliasOf"] = "first-encounter"
    bundles["home-cardigan"]["anchorVersion"] = 1
    bad_manifest = manifest_path.with_name("theme-contract-drift.json")
    bad_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    qml_source = qml_path.read_text(encoding="utf-8")
    qml_source = qml_source.replace(
        'case "desktopPetFocus": return 944.0 / 1666.0',
        'case "desktopPetFocus": return 944.0 / 1600.0',
        1,
    )
    qml_source = qml_source.replace(
        'case "desktopPetRest": return 1590.0 / 1672.0',
        'case "desktopPetRest": return 1589.0 / 1672.0',
        1,
    )
    qml_source = qml_source.replace(
        "root.outfitVerticalOffset * figureFrame.height",
        "0.0 /* deliberately broken slice alignment */",
        1,
    )
    bad_qml = qml_path.with_name("V03PetBody-contract-drift.qml")
    bad_qml.write_text(qml_source, encoding="utf-8")

    report = validate_outfit_assets(bad_manifest, bad_qml)

    errors = _error_text(report)
    assert report["passed"] is False
    assert "duplicate outfit hash must have one canonical and explicit aliases" in errors
    assert "outfit visual alias hash differs from target: rest-nightdress" in errors
    assert "outfit anchorVersion does not match v2: home-cardigan" in errors
    assert "QML outfit aspect ratio does not match pixels" in errors
    assert "QML outfit feet anchor does not match asset: rest-nightdress" in errors
    assert "all three breathing slices must share outfitVerticalOffset" in errors


def test_outfit_switching_preserves_runtime_anchors_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_outfit_ui.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(
        (PROJECT_ROOT / "artifacts" / "outfit-runtime-gate.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["passed"] is True
    assert report["platform"] == "offscreen"
    assert report["uniqueSourceCount"] == 5
    assert report["aliasSourcesExplicit"] is True
    foot_positions = []
    center_positions = []
    for outfit in report["outfits"].values():
        assert outfit["passed"] is True
        assert outfit["sourcesReady"] is True
        assert outfit["sliceOffsetsConsistent"] is True
        assert abs(outfit["frameRatio"] - outfit["expectedRatio"]) <= 1e-6
        foot_positions.append(float(outfit["footWorldY"]))
        center_positions.append(float(outfit["centerWorldX"]))
    assert max(foot_positions) - min(foot_positions) <= 0.05
    assert max(center_positions) - min(center_positions) <= 0.05
