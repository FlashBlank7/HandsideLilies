from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image

try:
    # Direct script execution puts ``scripts`` on sys.path.
    from verify_pose_assets import _validate_asset
except ModuleNotFoundError:  # Imported by pytest as scripts.verify_outfit_assets.
    from scripts.verify_pose_assets import _validate_asset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_THEME = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
DEFAULT_QML = PROJECT_ROOT / "qml" / "V03PetBody.qml"
DEFAULT_REPORT = PROJECT_ROOT / "artifacts" / "outfit-asset-gate.json"


def _property_body(source: str, property_type: str, name: str) -> tuple[str, list[str]]:
    pattern = re.compile(
        rf"readonly\s+property\s+{re.escape(property_type)}\s+{re.escape(name)}"
        r"\s*:\s*\{(?P<body>.*?)\n\s*\}",
        re.DOTALL,
    )
    match = pattern.search(source)
    if match is None:
        return "", [f"V03PetBody.qml does not declare {name}"]
    return match.group("body"), []


def _string_switch(source: str, name: str) -> tuple[dict[str, str], str, list[str]]:
    body, errors = _property_body(source, "string", name)
    if not body:
        return {}, "", errors
    cases = {
        case.group("key"): case.group("value")
        for case in re.finditer(
            r'case\s+"(?P<key>[^"]+)"\s*:\s*return\s+"(?P<value>[^"]+)"',
            body,
        )
    }
    default = re.search(r'default\s*:\s*return\s+"(?P<value>[^"]+)"', body)
    if default is None:
        errors.append(f"{name} has no string default")
    return cases, default.group("value") if default else "", errors


def _fraction_switch(
    source: str,
    name: str,
    default_key: str,
) -> tuple[dict[str, dict[str, float]], list[str]]:
    body, errors = _property_body(source, "real", name)
    if not body:
        return {}, errors
    result: dict[str, dict[str, float]] = {}
    pattern = re.compile(
        r'case\s+"(?P<key>[^"]+)"\s*:\s*return\s*'
        r"(?P<numerator>\d+(?:\.\d+)?)\s*/\s*(?P<denominator>\d+(?:\.\d+)?)"
    )
    for match in pattern.finditer(body):
        denominator = float(match.group("denominator"))
        if denominator <= 0:
            errors.append(f"{name} denominator must be positive: {match.group('key')}")
            continue
        numerator = float(match.group("numerator"))
        result[match.group("key")] = {
            "numerator": numerator,
            "denominator": denominator,
            "value": numerator / denominator,
            "ratio": numerator / denominator,
        }
    default = re.search(
        r"default\s*:\s*return\s*(?P<numerator>\d+(?:\.\d+)?)"
        r"\s*/\s*(?P<denominator>\d+(?:\.\d+)?)",
        body,
    )
    if default is None:
        errors.append(f"{name} has no fractional default")
    else:
        denominator = float(default.group("denominator"))
        numerator = float(default.group("numerator"))
        if denominator <= 0:
            errors.append(f"{name} default denominator must be positive")
        else:
            result[default_key] = {
                "numerator": numerator,
                "denominator": denominator,
                "value": numerator / denominator,
                "ratio": numerator / denominator,
            }
    return result, errors


def _fraction_scalar(source: str, name: str) -> tuple[float | None, list[str]]:
    match = re.search(
        rf"readonly\s+property\s+real\s+{re.escape(name)}\s*:\s*"
        r"(?P<numerator>\d+(?:\.\d+)?)\s*/\s*(?P<denominator>\d+(?:\.\d+)?)",
        source,
    )
    if match is None:
        return None, [f"V03PetBody.qml does not declare fractional {name}"]
    denominator = float(match.group("denominator"))
    if denominator <= 0:
        return None, [f"{name} denominator must be positive"]
    return float(match.group("numerator")) / denominator, []


def _number_scalar(
    source: str,
    property_type: str,
    name: str,
) -> tuple[float | None, list[str]]:
    match = re.search(
        rf"readonly\s+property\s+{re.escape(property_type)}\s+{re.escape(name)}"
        r"\s*:\s*(?P<value>\d+(?:\.\d+)?)",
        source,
    )
    if match is None:
        return None, [f"V03PetBody.qml does not declare literal {name}"]
    return float(match.group("value")), []


def _solid_alignment(path: Path, threshold: int) -> dict[str, Any]:
    with Image.open(path) as source:
        source.load()
        alpha = source.getchannel("A")
        width, height = source.size
        mask = alpha.point(lambda value: 255 if value >= threshold else 0)
        box = mask.getbbox()
    if box is None:
        return {"bbox": None, "solidCenterX": None, "feetY": None}
    left, top, right, bottom = box
    return {
        "bbox": [left, top, right, bottom],
        "solidCenterX": (left + right) / (2.0 * width),
        "feetY": bottom / height,
    }


def _pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    try:
        result = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) and 0.0 <= item <= 1.0 for item in result):
        return None
    return result


def validate_outfit_assets(theme_path: Path, qml_path: Path) -> dict[str, Any]:
    theme_path = theme_path.resolve()
    qml_path = qml_path.resolve()
    errors: list[str] = []
    try:
        manifest = json.loads(theme_path.read_text(encoding="utf-8"))
        qml_source = qml_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return {
            "schemaVersion": 1,
            "theme": str(theme_path),
            "qml": str(qml_path),
            "outfits": {},
            "errors": [f"cannot read outfit inputs: {exc}"],
            "passed": False,
        }

    character = manifest.get("character", {})
    if not isinstance(character, dict):
        character = {}
        errors.append("character must be an object")
    gate_version = character.get("outfitAssetGateVersion")
    if gate_version != 1:
        errors.append("character.outfitAssetGateVersion must be 1")
    outfit_ids = {str(value) for value in character.get("outfits", [])}
    default_outfit = str(character.get("defaultOutfit", ""))
    bundles = character.get("outfitBundles", {})
    if not isinstance(bundles, dict):
        bundles = {}
        errors.append("character.outfitBundles must be an object")
    specs = character.get("outfitArtworkSpecs", {})
    if not isinstance(specs, dict) or not specs:
        specs = {}
        errors.append("character.outfitArtworkSpecs must be a non-empty object")
    if set(bundles) != outfit_ids:
        errors.append("character.outfits and outfitBundles must contain exactly the same ids")
    if set(specs) != outfit_ids:
        errors.append("character.outfits and outfitArtworkSpecs must contain exactly the same ids")
    if default_outfit not in outfit_ids:
        errors.append("character.defaultOutfit must be a declared outfit")

    alignment = character.get("outfitAlignment", {})
    if not isinstance(alignment, dict):
        alignment = {}
        errors.append("character.outfitAlignment must be an object")
    try:
        anchor_version = int(alignment.get("anchorVersion", 0))
        solid_threshold = int(alignment.get("solidAlphaThreshold", 0))
        canonical_center = float(alignment.get("canonicalSolidCenterX"))
        canonical_feet = float(alignment.get("canonicalFeetY"))
    except (TypeError, ValueError):
        anchor_version = 0
        solid_threshold = 0
        canonical_center = math.nan
        canonical_feet = math.nan
        errors.append("outfitAlignment numeric values are invalid")
    canonical_outfit = str(alignment.get("canonicalOutfit", ""))
    support_cord = _pair(alignment.get("supportCord"))
    if anchor_version < 1:
        errors.append("outfitAlignment.anchorVersion must be positive")
    if not 128 <= solid_threshold <= 254:
        errors.append("outfitAlignment.solidAlphaThreshold must be 128..254")
    if canonical_outfit != default_outfit:
        errors.append("outfitAlignment canonicalOutfit must equal defaultOutfit")
    if not math.isfinite(canonical_center) or not 0.0 <= canonical_center <= 1.0:
        errors.append("outfitAlignment canonicalSolidCenterX must be normalized")
    if not math.isfinite(canonical_feet) or not 0.0 <= canonical_feet <= 1.0:
        errors.append("outfitAlignment canonicalFeetY must be normalized")
    if support_cord is None:
        errors.append("outfitAlignment.supportCord must be a normalized pair")

    assets_raw = manifest.get("assets", {})
    if not isinstance(assets_raw, dict):
        assets_raw = {}
        errors.append("assets must be an object")
    assets = {str(key): str(value) for key, value in assets_raw.items()}

    qml_map, qml_default_asset, qml_errors = _string_switch(qml_source, "outfitAssetKey")
    errors.extend(qml_errors)
    expected_case_ids = outfit_ids - {default_outfit}
    if set(qml_map) != expected_case_ids:
        errors.append("QML outfitAssetKey cases do not match non-default outfit ids")
    expected_default_asset = str(dict(bundles.get(default_outfit, {})).get("asset", ""))
    if qml_default_asset != expected_default_asset:
        errors.append("QML default outfit asset does not match the default outfit bundle")

    bundle_asset_keys = {
        str(dict(value).get("asset", ""))
        for value in bundles.values()
        if isinstance(value, dict)
    }
    qml_ratios, ratio_errors = _fraction_switch(
        qml_source, "outfitArtworkAspectRatio", expected_default_asset
    )
    qml_centers, center_errors = _fraction_switch(
        qml_source, "outfitSolidCenterX", expected_default_asset
    )
    qml_feet, feet_errors = _fraction_switch(
        qml_source, "outfitFeetY", expected_default_asset
    )
    errors.extend(ratio_errors + center_errors + feet_errors)
    for name, values in (
        ("outfitArtworkAspectRatio", qml_ratios),
        ("outfitSolidCenterX", qml_centers),
        ("outfitFeetY", qml_feet),
    ):
        if set(values) != bundle_asset_keys:
            errors.append(f"QML {name} keys do not match outfit bundle asset keys")

    qml_anchor_version, value_errors = _number_scalar(
        qml_source, "int", "outfitAnchorVersion"
    )
    errors.extend(value_errors)
    qml_support_x, value_errors = _number_scalar(qml_source, "real", "outfitSupportCordX")
    errors.extend(value_errors)
    qml_support_y, value_errors = _number_scalar(qml_source, "real", "outfitSupportCordY")
    errors.extend(value_errors)
    qml_canonical_center, value_errors = _fraction_scalar(
        qml_source, "canonicalOutfitSolidCenterX"
    )
    errors.extend(value_errors)
    qml_canonical_feet, value_errors = _fraction_scalar(qml_source, "canonicalOutfitFeetY")
    errors.extend(value_errors)
    if qml_anchor_version is None or int(qml_anchor_version) != anchor_version:
        errors.append("QML outfitAnchorVersion does not match manifest alignment")
    if support_cord is not None and (
        qml_support_x is None
        or qml_support_y is None
        or abs(qml_support_x - support_cord[0]) > 1e-9
        or abs(qml_support_y - support_cord[1]) > 1e-9
    ):
        errors.append("QML support-cord anchor does not match manifest alignment")
    if qml_canonical_center is None or abs(qml_canonical_center - canonical_center) > 1e-9:
        errors.append("QML canonical solid center does not match manifest alignment")
    if qml_canonical_feet is None or abs(qml_canonical_feet - canonical_feet) > 1e-9:
        errors.append("QML canonical feet anchor does not match manifest alignment")
    if qml_source.count("source: root.outfitSource") != 3:
        errors.append("all three breathing slices must use root.outfitSource")
    if qml_source.count("root.outfitVerticalOffset * figureFrame.height") != 3:
        errors.append("all three breathing slices must share outfitVerticalOffset")
    if qml_source.count("root.outfitHorizontalOffset * figureFrame.width") != 3:
        errors.append("all three breathing slices must share outfitHorizontalOffset")

    outfit_reports: dict[str, Any] = {}
    hash_groups: dict[str, list[str]] = {}
    for outfit_id in sorted(outfit_ids):
        bundle = bundles.get(outfit_id)
        spec = specs.get(outfit_id)
        if not isinstance(bundle, dict) or not isinstance(spec, dict):
            errors.append(f"outfit bundle/spec must be objects: {outfit_id}")
            continue
        asset_key = str(bundle.get("asset", ""))
        if str(spec.get("asset", "")) != asset_key:
            errors.append(f"outfit spec asset does not match bundle: {outfit_id}")
        try:
            bundle_anchor = int(bundle.get("anchorVersion", 0))
            spec_anchor = int(spec.get("anchorVersion", 0))
        except (TypeError, ValueError):
            bundle_anchor = 0
            spec_anchor = 0
        if bundle_anchor != anchor_version or spec_anchor != anchor_version:
            errors.append(f"outfit anchorVersion does not match v{anchor_version}: {outfit_id}")
        expected_asset = expected_default_asset if outfit_id == default_outfit else qml_map.get(outfit_id)
        if expected_asset != asset_key:
            errors.append(f"QML outfit mapping does not match bundle: {outfit_id}")

        asset_report, asset_errors = _validate_asset(
            asset_key=asset_key,
            definition=spec,
            assets=assets,
            theme_root=theme_path.parent,
            qml_ratio=qml_ratios.get(asset_key),
            asset_label="outfit",
            legacy_baselines={},
        )
        errors.extend(asset_errors)
        asset_report["outfitId"] = outfit_id
        asset_report["anchorVersion"] = bundle_anchor
        asset_report["implementationStatus"] = str(bundle.get("implementationStatus", ""))
        asset_report["visualAliasOf"] = str(bundle.get("visualAliasOf", ""))
        resolved_value = asset_report.get("resolvedPath")
        resolved = Path(str(resolved_value)) if resolved_value else None
        if resolved is not None and resolved.is_file():
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            asset_report["sha256"] = digest
            hash_groups.setdefault(digest, []).append(outfit_id)
            if str(spec.get("sha256", "")).casefold() != digest:
                errors.append(f"outfit sha256 does not match reviewed asset: {outfit_id}")
                asset_report.setdefault("errors", []).append(
                    f"outfit sha256 does not match reviewed asset: {outfit_id}"
                )
                asset_report["passed"] = False
            calculated = _solid_alignment(resolved, solid_threshold)
            asset_report["alignment"] = calculated
            declared_alignment = spec.get("alignment", {})
            if not isinstance(declared_alignment, dict):
                declared_alignment = {}
            try:
                declared_center = float(declared_alignment.get("solidCenterX"))
                declared_feet = float(declared_alignment.get("feetY"))
            except (TypeError, ValueError):
                declared_center = math.nan
                declared_feet = math.nan
            calculated_center = calculated.get("solidCenterX")
            calculated_feet = calculated.get("feetY")
            if (
                calculated_center is None
                or not math.isfinite(declared_center)
                or abs(float(calculated_center) - declared_center) > 1e-9
            ):
                errors.append(f"outfit solid-center anchor drift: {outfit_id}")
            if (
                calculated_feet is None
                or not math.isfinite(declared_feet)
                or abs(float(calculated_feet) - declared_feet) > 1e-9
            ):
                errors.append(f"outfit feet anchor drift: {outfit_id}")
            qml_center = qml_centers.get(asset_key, {}).get("value")
            qml_feet_value = qml_feet.get(asset_key, {}).get("value")
            if qml_center is None or calculated_center is None or abs(
                float(qml_center) - float(calculated_center)
            ) > 1e-9:
                errors.append(f"QML outfit solid center does not match asset: {outfit_id}")
            if qml_feet_value is None or calculated_feet is None or abs(
                float(qml_feet_value) - float(calculated_feet)
            ) > 1e-9:
                errors.append(f"QML outfit feet anchor does not match asset: {outfit_id}")
        outfit_reports[outfit_id] = asset_report

    for outfit_id, report in outfit_reports.items():
        status = str(report.get("implementationStatus", ""))
        alias_of = str(report.get("visualAliasOf", ""))
        if status == "visual-alias":
            target = outfit_reports.get(alias_of)
            if target is None:
                errors.append(f"outfit visual alias target is missing: {outfit_id}")
            elif report.get("sha256") != target.get("sha256"):
                errors.append(f"outfit visual alias hash differs from target: {outfit_id}")
        elif status == "production":
            if alias_of:
                errors.append(f"production outfit cannot declare visualAliasOf: {outfit_id}")
        else:
            errors.append(f"outfit implementationStatus is invalid: {outfit_id}")

    for digest, grouped_outfits in hash_groups.items():
        if len(grouped_outfits) < 2:
            continue
        canonical = [
            outfit_id
            for outfit_id in grouped_outfits
            if outfit_reports[outfit_id].get("implementationStatus") == "production"
        ]
        aliases = [
            outfit_id
            for outfit_id in grouped_outfits
            if outfit_reports[outfit_id].get("implementationStatus") == "visual-alias"
        ]
        if len(canonical) != 1 or len(aliases) != len(grouped_outfits) - 1:
            errors.append(
                f"duplicate outfit hash must have one canonical and explicit aliases: {digest}"
            )
        elif any(
            outfit_reports[outfit_id].get("visualAliasOf") != canonical[0]
            for outfit_id in aliases
        ):
            errors.append(f"duplicate outfit aliases must target {canonical[0]}: {digest}")

    # Recompute per-outfit pass after relationship and anchor checks have been
    # gathered.  Top-level errors remain authoritative for cross-outfit rules.
    passed = not errors and bool(outfit_reports)
    return {
        "schemaVersion": 1,
        "theme": str(theme_path),
        "qml": str(qml_path),
        "gateVersion": gate_version,
        "anchorVersion": anchor_version,
        "defaultOutfit": default_outfit,
        "qmlOutfitMap": {**qml_map, default_outfit: qml_default_asset},
        "hashGroups": {key: sorted(value) for key, value in sorted(hash_groups.items())},
        "outfits": outfit_reports,
        "errors": errors,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production Lilies outfit artwork")
    parser.add_argument("--theme", type=Path, default=DEFAULT_THEME)
    parser.add_argument("--qml", type=Path, default=DEFAULT_QML)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()
    report = validate_outfit_assets(args.theme, args.qml)
    if not args.no_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
