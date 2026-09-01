from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lilies.core.themes import validate_pose_click_mask


DEFAULT_THEME = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
DEFAULT_QML = PROJECT_ROOT / "qml" / "V03PetBody.qml"
DEFAULT_REPORT = PROJECT_ROOT / "artifacts" / "pose-asset-gate.json"

_QML_RATIO_BLOCK = re.compile(
    r"readonly\s+property\s+real\s+poseArtworkAspectRatio\s*:\s*\{(?P<body>.*?)\n\s*\}",
    re.DOTALL,
)
_QML_RATIO_CASE = re.compile(
    r'case\s+"(?P<key>[^"]+)"\s*:\s*return\s*'
    r"(?P<numerator>\d+(?:\.\d+)?)\s*/\s*(?P<denominator>\d+(?:\.\d+)?)"
)
_FORBIDDEN_RUNTIME_PATH_PARTS = {
    "art-reference",
    "concept",
    "generated",
    "generated-v0.2",
    "generated-v0.3",
}
_LEGACY_POSE_BASELINES = {
    "posePerchProne": (365, 364),
    "poseTitleSit": (350, 592),
    "poseEdgePeek": (227, 620),
    "poseListeningLive": (314, 614),
}
_PRODUCTION_MIN_SHORTEST_SIDE = 512
_PRODUCTION_MIN_LONGEST_SIDE = 1024
_PRODUCTION_MIN_PIXEL_AREA = 512 * 1024
_SPRITE_ALPHA_THRESHOLD = 16
_SPRITE_QUADRANTS = {"top-left", "top-right", "bottom-left", "bottom-right"}
_SILHOUETTE_SAMPLE_SIZE = 128
_MIN_CLICK_MASK_SUBJECT_COVERAGE = 0.90
_MIN_CLICK_MASK_SUBJECT_PRECISION = 0.20


def _number(value: Any, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def _pair(value: Any, fallback: tuple[int, int]) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        return fallback
    try:
        width, height = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return fallback
    return width, height


def _quad(value: Any, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4:
        return fallback
    try:
        left, top, width, height = (int(item) for item in value)
    except (TypeError, ValueError):
        return fallback
    return left, top, width, height


def _normalized_pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    result = (_number(value[0], -1.0), _number(value[1], -1.0))
    return result if all(0.0 <= item <= 1.0 for item in result) else None


def _normalized_rect(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    result = tuple(_number(item, -1.0) for item in value)
    left, top, width, height = result
    if (
        left < 0.0
        or top < 0.0
        or width <= 0.0
        or height <= 0.0
        or left + width > 1.0
        or top + height > 1.0
    ):
        return None
    return left, top, width, height


def _meaningful_alpha_bbox(alpha: Image.Image, threshold: int) -> tuple[int, int, int, int] | None:
    return alpha.point(lambda value: 255 if value >= threshold else 0).getbbox()


def _click_mask_relation(
    alpha: Image.Image,
    click_mask: Any,
    *,
    threshold: int = _SPRITE_ALPHA_THRESHOLD,
) -> dict[str, float | int]:
    """Compare a declarative hit mask with the bitmap's meaningful alpha.

    The runtime mask language is deliberately coarse, so the gate allows a
    small antialiased fringe outside it.  It still requires the mask to cover
    nearly all visible subject pixels and rejects masks that mostly describe
    transparent canvas (an invisible click island).
    """

    width, height = alpha.size
    subject = alpha.point(lambda value: 255 if value >= threshold else 0)
    rendered_mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(rendered_mask)
    mask = click_mask if isinstance(click_mask, dict) else {}

    def point(value: Any) -> tuple[float, float] | None:
        normalized = _normalized_pair(value)
        if normalized is None:
            return None
        return normalized[0] * (width - 1), normalized[1] * (height - 1)

    def draw_rect(value: Any) -> None:
        normalized = _normalized_rect(value)
        if normalized is None:
            return
        left, top, rect_width, rect_height = normalized
        draw.rectangle(
            (
                left * (width - 1),
                top * (height - 1),
                (left + rect_width) * (width - 1),
                (top + rect_height) * (height - 1),
            ),
            fill=255,
        )

    def draw_ellipse(value: Any) -> None:
        if not isinstance(value, list) or len(value) != 4:
            return
        center_x, center_y, radius_x, radius_y = (
            _number(item, -1.0) for item in value
        )
        if (
            radius_x <= 0.0
            or radius_y <= 0.0
            or center_x - radius_x < 0.0
            or center_x + radius_x > 1.0
            or center_y - radius_y < 0.0
            or center_y + radius_y > 1.0
        ):
            return
        draw.ellipse(
            (
                (center_x - radius_x) * (width - 1),
                (center_y - radius_y) * (height - 1),
                (center_x + radius_x) * (width - 1),
                (center_y + radius_y) * (height - 1),
            ),
            fill=255,
        )

    mask_type = str(mask.get("type", ""))
    if mask_type == "rect":
        draw_rect(mask.get("rect"))
    elif mask_type == "ellipse":
        draw_ellipse(mask.get("ellipse"))
    for value in mask.get("rects", []) if isinstance(mask.get("rects", []), list) else []:
        draw_rect(value)
    for value in mask.get("ellipses", []) if isinstance(mask.get("ellipses", []), list) else []:
        draw_ellipse(value)
    for polygon in (
        mask.get("polygons", [])
        if isinstance(mask.get("polygons", []), list)
        else []
    ):
        if not isinstance(polygon, list) or len(polygon) < 3:
            continue
        points = [point(value) for value in polygon]
        if any(value is None for value in points):
            continue
        draw.polygon(points, fill=255)

    subject_pixels = subject.histogram()[255]
    mask_pixels = rendered_mask.histogram()[255]
    overlap_pixels = ImageChops.multiply(subject, rendered_mask).histogram()[255]
    return {
        "alphaThreshold": threshold,
        "subjectPixels": subject_pixels,
        "maskPixels": mask_pixels,
        "overlapPixels": overlap_pixels,
        "subjectCoverage": overlap_pixels / max(1, subject_pixels),
        "subjectPrecision": overlap_pixels / max(1, mask_pixels),
        "minimumSubjectCoverage": _MIN_CLICK_MASK_SUBJECT_COVERAGE,
        "minimumSubjectPrecision": _MIN_CLICK_MASK_SUBJECT_PRECISION,
    }


def _parse_qml_ratios(qml_path: Path) -> tuple[dict[str, dict[str, float]], list[str]]:
    errors: list[str] = []
    try:
        source = qml_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, [f"cannot read QML ratio source: {exc}"]
    match = _QML_RATIO_BLOCK.search(source)
    if match is None:
        return {}, ["V03PetBody.qml does not declare poseArtworkAspectRatio"]
    ratios: dict[str, dict[str, float]] = {}
    for case in _QML_RATIO_CASE.finditer(match.group("body")):
        key = case.group("key")
        numerator = float(case.group("numerator"))
        denominator = float(case.group("denominator"))
        if denominator <= 0:
            errors.append(f"QML aspect-ratio denominator must be positive: {key}")
            continue
        if key in ratios:
            errors.append(f"QML aspect ratio is declared more than once: {key}")
            continue
        ratios[key] = {
            "numerator": numerator,
            "denominator": denominator,
            "ratio": numerator / denominator,
        }
    return ratios, errors


def _transparent_corner_ratio(alpha: Image.Image) -> tuple[float, list[float]]:
    width, height = alpha.size
    sample_width = max(2, math.ceil(width * 0.05))
    sample_height = max(2, math.ceil(height * 0.05))
    boxes = (
        (0, 0, sample_width, sample_height),
        (width - sample_width, 0, width, sample_height),
        (0, height - sample_height, sample_width, height),
        (width - sample_width, height - sample_height, width, height),
    )
    ratios: list[float] = []
    for box in boxes:
        payload = alpha.crop(box).tobytes()
        ratios.append(sum(value == 0 for value in payload) / max(1, len(payload)))
    return min(ratios), ratios


def _edge_risk_metrics(rgba: Image.Image) -> dict[str, float | int]:
    width, height = rgba.size
    pixels = rgba.load()
    transparent_count = 0
    transparent_rgb_leak = 0
    boundary_count = 0
    green_boundary_count = 0
    white_fringe_count = 0

    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0:
                transparent_count += 1
                if max(red, green, blue) > 12:
                    transparent_rgb_leak += 1
                continue

            neighbours: list[tuple[int, int, int, int]] = []
            touches_transparency = False
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    if nx == x and ny == y:
                        continue
                    neighbour = pixels[nx, ny]
                    neighbours.append(neighbour)
                    if neighbour[3] == 0:
                        touches_transparency = True
            # Alpha 254 is commonly used as a fully covered export value and
            # must not dilute fringe ratios by turning the whole subject into
            # a "boundary".  Only the actual silhouette or visibly
            # translucent pixels participate in edge-risk measurements.
            if not (touches_transparency or 0 < alpha < 240):
                continue

            boundary_count += 1
            if green >= 90 and green - red >= 30 and green - blue >= 24:
                green_boundary_count += 1

            # A white subject is legitimate.  Flag only a translucent, nearly
            # neutral white boundary pixel whose opaque inward neighbour is
            # substantially darker: this is the characteristic matte fringe,
            # not white hair or a white dress continuing to the silhouette.
            bright_neutral = (
                0 < alpha < 230
                and min(red, green, blue) >= 218
                and max(red, green, blue) - min(red, green, blue) <= 20
            )
            has_dark_interior = any(
                neighbour[3] >= 230
                and (
                    0.2126 * neighbour[0]
                    + 0.7152 * neighbour[1]
                    + 0.0722 * neighbour[2]
                )
                <= 155
                for neighbour in neighbours
            )
            if bright_neutral and has_dark_interior:
                white_fringe_count += 1

    return {
        "transparentPixels": transparent_count,
        "transparentRgbLeakPixels": transparent_rgb_leak,
        "transparentRgbLeakRatio": transparent_rgb_leak / max(1, transparent_count),
        "boundaryPixels": boundary_count,
        "greenBoundaryPixels": green_boundary_count,
        "greenBoundaryRatio": green_boundary_count / max(1, boundary_count),
        "whiteFringeRiskPixels": white_fringe_count,
        "whiteFringeRiskRatio": white_fringe_count / max(1, boundary_count),
    }


def _normalized_silhouette(path: Path, *, sample_size: int = _SILHOUETTE_SAMPLE_SIZE) -> Image.Image:
    """Return a centred, aspect-preserving binary alpha silhouette.

    Normalising the meaningful alpha bounding box makes the comparison
    independent of transparent canvas padding, while preserving aspect ratio
    means a stretched copy cannot masquerade as a new pose.  Nearest-neighbour
    sampling keeps the result binary and deterministic across Pillow builds.
    """

    with Image.open(path) as source:
        source.load()
        rgba = source.copy() if source.mode == "RGBA" else source.convert("RGBA")
    alpha = rgba.getchannel("A").point(
        lambda value: 255 if value >= _SPRITE_ALPHA_THRESHOLD else 0
    )
    bounds = alpha.getbbox()
    if bounds is None:
        return Image.new("L", (sample_size, sample_size), 0)
    cropped = alpha.crop(bounds)
    inset = max(2, sample_size // 16)
    available = sample_size - inset * 2
    scale = min(available / max(1, cropped.width), available / max(1, cropped.height))
    width = max(1, int(round(cropped.width * scale)))
    height = max(1, int(round(cropped.height * scale)))
    resized = cropped.resize((width, height), Image.Resampling.NEAREST)
    result = Image.new("L", (sample_size, sample_size), 0)
    result.paste(resized, ((sample_size - width) // 2, (sample_size - height) // 2))
    return result


def _silhouette_distinctness(candidate: Path, baseline: Path) -> dict[str, float | bool]:
    """Measure silhouette overlap against both baseline orientations."""

    candidate_mask = _normalized_silhouette(candidate)
    baseline_mask = _normalized_silhouette(baseline)
    mirrored_mask = baseline_mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    candidate_bits = candidate_mask.tobytes()

    def compare(other: Image.Image) -> tuple[float, float]:
        other_bits = other.tobytes()
        intersection = sum(
            left > 0 and right > 0
            for left, right in zip(candidate_bits, other_bits)
        )
        union = sum(
            left > 0 or right > 0
            for left, right in zip(candidate_bits, other_bits)
        )
        xor = sum(
            (left > 0) != (right > 0)
            for left, right in zip(candidate_bits, other_bits)
        )
        return intersection / max(1, union), xor / max(1, union)

    direct_iou, direct_difference = compare(baseline_mask)
    mirrored_iou, mirrored_difference = compare(mirrored_mask)
    if mirrored_iou > direct_iou:
        best_iou, best_difference, matched_mirror = (
            mirrored_iou,
            mirrored_difference,
            True,
        )
    else:
        best_iou, best_difference, matched_mirror = (
            direct_iou,
            direct_difference,
            False,
        )
    return {
        "intersectionOverUnion": best_iou,
        "differenceRatio": best_difference,
        "matchedMirroredBaseline": matched_mirror,
        "directIntersectionOverUnion": direct_iou,
        "mirroredIntersectionOverUnion": mirrored_iou,
    }


def _validate_optional_declaration(
    *,
    asset_key: str,
    definition: dict[str, Any],
    assets: dict[str, str],
    theme_root: Path,
    qml_ratio: dict[str, float] | None,
) -> tuple[dict[str, Any], list[str], Path | None]:
    """Validate a dormant optional asset without requiring the bitmap yet."""

    errors: list[str] = []
    relative_value = assets.get(asset_key, "")
    report: dict[str, Any] = {
        "assetKey": asset_key,
        "manifestPath": relative_value,
        "optional": True,
        "validated": False,
        "status": "dormant-missing",
        "passed": False,
    }
    resolved: Path | None = None
    if not bool(definition.get("optional")):
        errors.append(f"optional pose spec must declare optional=true: {asset_key}")
    if not relative_value:
        errors.append(f"optional pose asset key is missing from assets: {asset_key}")
    else:
        relative = Path(relative_value)
        if relative.is_absolute():
            errors.append(f"optional pose asset path must be relative: {asset_key}")
        resolved = (theme_root / relative).resolve()
        try:
            resolved.relative_to(theme_root.resolve())
        except ValueError:
            errors.append(f"optional pose asset path escapes theme root: {asset_key}")
            resolved = None
        if relative.suffix.casefold() != ".png":
            errors.append(f"runtime optional pose must be a PNG: {asset_key}")
        lowered_parts = {part.casefold() for part in relative.parts}
        lowered_name = relative.name.casefold()
        if lowered_parts & _FORBIDDEN_RUNTIME_PATH_PARTS or any(
            marker in lowered_name for marker in ("concept", "checkerboard", "chroma")
        ):
            errors.append(
                f"concept/generated/chroma artwork cannot be a runtime optional pose: {asset_key}"
            )

    declared_size = _pair(definition.get("pixelSize"), (0, 0))
    minimum_size = _pair(definition.get("minimumSize"), (0, 0))
    declared_ratio = _number(definition.get("aspectRatio"), 0.0)
    if str(definition.get("resolutionTier", "")) != "production-v1":
        errors.append(f"optional pose resolutionTier must be production-v1: {asset_key}")
    if (
        min(minimum_size, default=0) < _PRODUCTION_MIN_SHORTEST_SIDE
        or max(minimum_size, default=0) < _PRODUCTION_MIN_LONGEST_SIDE
        or minimum_size[0] * minimum_size[1] < _PRODUCTION_MIN_PIXEL_AREA
    ):
        errors.append(f"optional pose minimumSize is below production policy: {asset_key}")
    actual_declared_ratio = declared_size[0] / max(1, declared_size[1])
    if declared_size[0] <= 0 or declared_size[1] <= 0 or abs(
        actual_declared_ratio - declared_ratio
    ) > 1e-6:
        errors.append(f"optional pose aspectRatio does not match pixelSize: {asset_key}")
    if (
        qml_ratio is None
        or abs(qml_ratio["numerator"] - declared_size[0]) > 1e-6
        or abs(qml_ratio["denominator"] - declared_size[1]) > 1e-6
    ):
        errors.append(f"QML optional pose aspect ratio does not match pixelSize: {asset_key}")
    if _normalized_pair(definition.get("anchor")) is None:
        errors.append(f"optional pose anchor must be a normalized pair: {asset_key}")
    if _normalized_pair(definition.get("cordAnchor")) is None:
        errors.append(f"optional pose cordAnchor must be a normalized pair: {asset_key}")

    silhouette_gate = definition.get("silhouetteGate", {})
    if not isinstance(silhouette_gate, dict):
        silhouette_gate = {}
        errors.append(f"optional pose silhouetteGate must be an object: {asset_key}")
    baseline_key = str(silhouette_gate.get("distinctFromAsset", ""))
    maximum_iou = _number(silhouette_gate.get("maxIntersectionOverUnion"), -1.0)
    minimum_difference = _number(silhouette_gate.get("minDifferenceRatio"), -1.0)
    if not baseline_key or baseline_key == asset_key or baseline_key not in assets:
        errors.append(f"optional pose distinct baseline is invalid: {asset_key}")
    if not 0.35 <= maximum_iou <= 0.90:
        errors.append(f"optional pose maxIntersectionOverUnion is outside policy: {asset_key}")
    if not 0.10 <= minimum_difference <= 0.65:
        errors.append(f"optional pose minDifferenceRatio is outside policy: {asset_key}")
    report.update(
        {
            "declaredPixelSize": list(declared_size),
            "minimumSize": list(minimum_size),
            "declaredAspectRatio": declared_ratio,
            "qmlAspectRatio": qml_ratio,
            "silhouetteBaselineAsset": baseline_key,
            "silhouetteThresholds": {
                "maxIntersectionOverUnion": maximum_iou,
                "minDifferenceRatio": minimum_difference,
            },
        }
    )
    report["errors"] = list(errors)
    report["passed"] = not errors
    return report, errors, resolved


def _validate_asset(
    *,
    asset_key: str,
    definition: dict[str, Any],
    assets: dict[str, str],
    theme_root: Path,
    qml_ratio: dict[str, float] | None,
    asset_label: str = "pose",
    legacy_baselines: dict[str, tuple[int, int]] | None = None,
    requires_qml_ratio: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    relative_value = assets.get(asset_key, "")
    relative = Path(relative_value) if relative_value else Path()
    report: dict[str, Any] = {
        "assetKey": asset_key,
        "manifestPath": relative_value,
        "passed": False,
    }
    if not relative_value:
        errors.append(f"{asset_label} asset key is missing from assets: {asset_key}")
        report["errors"] = list(errors)
        return report, errors
    path_is_safe = True
    if relative.is_absolute():
        errors.append(f"{asset_label} asset path must be relative: {asset_key}")
        path_is_safe = False
    resolved = (theme_root / relative).resolve()
    try:
        resolved.relative_to(theme_root.resolve())
    except ValueError:
        errors.append(f"{asset_label} asset path escapes theme root: {asset_key}")
        path_is_safe = False
    report["resolvedPath"] = str(resolved)
    if not path_is_safe:
        report["errors"] = list(errors)
        return report, errors
    lowered_parts = {part.casefold() for part in relative.parts}
    lowered_name = relative.name.casefold()
    if lowered_parts & _FORBIDDEN_RUNTIME_PATH_PARTS or any(
        marker in lowered_name for marker in ("concept", "checkerboard", "chroma")
    ):
        errors.append(
            f"concept/generated/chroma artwork cannot be a runtime {asset_label}: {asset_key}"
        )
    if relative.suffix.casefold() != ".png":
        errors.append(f"runtime {asset_label} must be a PNG: {asset_key}")
    if not resolved.is_file():
        errors.append(f"{asset_label} asset does not exist: {asset_key}")
        report["errors"] = list(errors)
        return report, errors

    declared_size = _pair(definition.get("pixelSize"), (0, 0))
    minimum_size = _pair(definition.get("minimumSize"), (0, 0))
    resolution_tier = str(definition.get("resolutionTier", ""))
    declared_ratio = _number(definition.get("aspectRatio"), 0.0)
    quality = definition.get("quality", {})
    if not isinstance(quality, dict):
        quality = {}
        errors.append(f"{asset_label} quality declaration must be an object: {asset_key}")
    min_transparent_ratio = _number(quality.get("minTransparentRatio"), 0.15)
    min_solid_ratio = _number(quality.get("minSolidRatio"), 0.05)
    min_corner_ratio = _number(quality.get("minTransparentCornerRatio"), 0.98)
    max_hidden_leak = _number(quality.get("maxTransparentRgbLeakRatio"), 0.001)
    max_green_risk = _number(quality.get("maxGreenBoundaryRatio"), 0.002)
    max_white_risk = _number(quality.get("maxWhiteFringeRiskRatio"), 0.01)
    required_quality = {
        "minTransparentRatio",
        "minSolidRatio",
        "minTransparentCornerRatio",
        "maxTransparentRgbLeakRatio",
        "maxGreenBoundaryRatio",
        "maxWhiteFringeRiskRatio",
    }
    missing_quality = sorted(required_quality - set(quality))
    if missing_quality:
        errors.append(
            f"{asset_label} quality thresholds are incomplete for {asset_key}: {missing_quality}"
        )
    policy_ranges = {
        "minTransparentRatio": (min_transparent_ratio, 0.10, 0.95),
        "minSolidRatio": (min_solid_ratio, 0.05, 0.95),
        "minTransparentCornerRatio": (min_corner_ratio, 0.98, 1.0),
        "maxTransparentRgbLeakRatio": (max_hidden_leak, 0.0, 0.001),
        "maxGreenBoundaryRatio": (max_green_risk, 0.0, 0.002),
        "maxWhiteFringeRiskRatio": (max_white_risk, 0.0, 0.01),
    }
    for name, (value, minimum, maximum) in policy_ranges.items():
        if not minimum <= value <= maximum:
            errors.append(
                f"{asset_label} quality threshold is outside gate policy for {asset_key}: "
                f"{name}={value} (expected {minimum}..{maximum})"
            )

    try:
        with Image.open(resolved) as source:
            source.load()
            image_format = str(source.format or "")
            image_mode = source.mode
            bands = tuple(source.getbands())
            width, height = source.size
            rgba = source.copy() if source.mode == "RGBA" else source.convert("RGBA")
    except (OSError, ValueError) as exc:
        errors.append(f"{asset_label} asset cannot be decoded: {asset_key}: {exc}")
        return report, errors

    alpha = rgba.getchannel("A")
    alpha_payload = alpha.tobytes()
    pixel_count = max(1, width * height)
    transparent_ratio = sum(value == 0 for value in alpha_payload) / pixel_count
    # Exporters often cap fully covered pixels at alpha 254.  Treat >= 192 as
    # solid subject coverage, while still requiring at least one true 255 via
    # alphaExtrema below.  This catches an all-faint/ghost export without
    # coupling the gate to a particular PNG encoder.
    solid_ratio = sum(value >= 192 for value in alpha_payload) / pixel_count
    exact_opaque_ratio = sum(value == 255 for value in alpha_payload) / pixel_count
    alpha_min, alpha_max = alpha.getextrema()
    corner_min, corner_values = _transparent_corner_ratio(alpha)
    edge_metrics = _edge_risk_metrics(rgba)
    meaningful_bbox = _meaningful_alpha_bbox(alpha, _SPRITE_ALPHA_THRESHOLD)
    sprites = definition.get("sprites")
    has_sprite_masks = isinstance(sprites, dict) and bool(sprites)
    declared_click_mask = definition.get("clickMask")
    has_declared_click_mask = (
        isinstance(declared_click_mask, dict) and bool(declared_click_mask)
    )
    # Pose artwork owns a manifest-authoritative bitmap mask, which is
    # validated separately by ``validate_pose_assets``.  Outfit artwork is
    # sliced into breathing layers and uses V03PetBody's geometric
    # ``silhouetteMask`` at runtime, so an absent per-bitmap mask is not an
    # empty hit region.  Only compare pixels when this asset actually declares
    # a bitmap mask; treating a missing declaration as ``{}`` produces a
    # synthetic zero-coverage failure for every outfit.
    click_mask_relation = (
        None
        if has_sprite_masks or not has_declared_click_mask
        else _click_mask_relation(alpha, declared_click_mask)
    )
    actual_ratio = width / max(1, height)

    report.update(
        {
            "resolvedPath": str(resolved),
            "format": image_format,
            "mode": image_mode,
            "bands": list(bands),
            "pixelSize": [width, height],
            "minimumSize": list(minimum_size),
            "resolutionTier": resolution_tier,
            "declaredPixelSize": list(declared_size),
            "actualAspectRatio": actual_ratio,
            "declaredAspectRatio": declared_ratio,
            "qmlAspectRatio": qml_ratio,
            "alphaExtrema": [alpha_min, alpha_max],
            "transparentRatio": transparent_ratio,
            "solidRatio": solid_ratio,
            "exactOpaqueRatio": exact_opaque_ratio,
            "cornerTransparentRatios": corner_values,
            "meaningfulAlphaBounds": (
                list(meaningful_bbox) if meaningful_bbox is not None else None
            ),
            "normalizedMeaningfulAlphaBounds": (
                [
                    meaningful_bbox[0] / width,
                    meaningful_bbox[1] / height,
                    meaningful_bbox[2] / width,
                    meaningful_bbox[3] / height,
                ]
                if meaningful_bbox is not None else None
            ),
            "clickMaskRelation": click_mask_relation,
            **edge_metrics,
        }
    )
    declared_sha256 = str(definition.get("sha256", "")).strip().casefold()
    actual_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
    report["sha256"] = actual_sha256
    if declared_sha256:
        report["declaredSha256"] = declared_sha256
        if not re.fullmatch(r"[0-9a-f]{64}", declared_sha256):
            errors.append(f"{asset_label} sha256 is not a lowercase hex digest: {asset_key}")
        elif actual_sha256 != declared_sha256:
            errors.append(f"{asset_label} sha256 drift for {asset_key}")

    if image_format.casefold() != "png":
        errors.append(f"{asset_label} asset is not encoded as PNG: {asset_key}")
    if image_mode != "RGBA" or bands != ("R", "G", "B", "A"):
        errors.append(f"{asset_label} asset must be true RGBA, not {image_mode}: {asset_key}")
    if alpha_min != 0 or alpha_max != 255:
        errors.append(f"{asset_label} alpha must contain both 0 and 255: {asset_key}")
    if meaningful_bbox is None:
        errors.append(f"{asset_label} has no meaningful alpha subject: {asset_key}")
    if click_mask_relation is not None and (
        float(click_mask_relation["subjectCoverage"])
        < _MIN_CLICK_MASK_SUBJECT_COVERAGE
    ):
        errors.append(
            f"{asset_label} clickMask covers too little meaningful alpha: {asset_key} "
            f"({click_mask_relation['subjectCoverage']:.4f} < "
            f"{_MIN_CLICK_MASK_SUBJECT_COVERAGE:.4f})"
        )
    if click_mask_relation is not None and (
        float(click_mask_relation["subjectPrecision"])
        < _MIN_CLICK_MASK_SUBJECT_PRECISION
    ):
        errors.append(
            f"{asset_label} clickMask has too little overlap with meaningful alpha: "
            f"{asset_key} ({click_mask_relation['subjectPrecision']:.4f} < "
            f"{_MIN_CLICK_MASK_SUBJECT_PRECISION:.4f})"
        )
    if declared_size != (width, height):
        errors.append(
            f"{asset_label} pixelSize drift for {asset_key}: "
            f"declared {declared_size}, actual {(width, height)}"
        )
    if minimum_size[0] <= 0 or minimum_size[1] <= 0:
        errors.append(f"{asset_label} minimumSize must contain positive integers: {asset_key}")
    elif width < minimum_size[0] or height < minimum_size[1]:
        errors.append(
            f"{asset_label} is below minimumSize for {asset_key}: "
            f"{(width, height)} < {minimum_size}"
        )
    if resolution_tier == "legacy-v1":
        legacy_map = _LEGACY_POSE_BASELINES if legacy_baselines is None else legacy_baselines
        legacy_size = legacy_map.get(asset_key)
        if legacy_size is None:
            errors.append(
                f"new {asset_label} assets cannot claim the legacy-v1 tier: {asset_key}"
            )
        elif declared_size != legacy_size or (width, height) != legacy_size:
            errors.append(
                f"legacy-v1 {asset_label} must match its immutable baseline "
                f"for {asset_key}: {legacy_size}"
            )
    elif resolution_tier == "production-v1":
        shortest = min(minimum_size)
        longest = max(minimum_size)
        area = minimum_size[0] * minimum_size[1]
        if (
            shortest < _PRODUCTION_MIN_SHORTEST_SIDE
            or longest < _PRODUCTION_MIN_LONGEST_SIDE
            or area < _PRODUCTION_MIN_PIXEL_AREA
        ):
            errors.append(
                f"production-v1 minimumSize is below 512x1024 / 524288px policy: {asset_key}"
            )
    else:
        errors.append(
            f"{asset_label} resolutionTier must be legacy-v1 or production-v1: {asset_key}"
        )
    if declared_ratio <= 0 or abs(actual_ratio - declared_ratio) > 1e-6:
        errors.append(f"{asset_label} manifest aspectRatio does not match pixels: {asset_key}")
    if requires_qml_ratio:
        qml_asset_label = "" if asset_label == "pose" else f"{asset_label} "
        if qml_ratio is None or abs(actual_ratio - qml_ratio["ratio"]) > 1e-6:
            errors.append(
                f"QML {qml_asset_label}aspect ratio does not match pixels: {asset_key}"
            )
        elif (
            abs(qml_ratio["numerator"] - width) > 1e-6
            or abs(qml_ratio["denominator"] - height) > 1e-6
        ):
            errors.append(
                f"QML {qml_asset_label}aspect ratio operands must equal pixelSize: {asset_key}"
            )
    if transparent_ratio < min_transparent_ratio:
        errors.append(f"{asset_label} has too little real transparency: {asset_key}")
    if solid_ratio < min_solid_ratio:
        errors.append(f"{asset_label} has too little solid subject content: {asset_key}")
    if corner_min < min_corner_ratio:
        errors.append(f"{asset_label} corners are not transparent enough: {asset_key}")
    if float(edge_metrics["transparentRgbLeakRatio"]) > max_hidden_leak:
        errors.append(f"{asset_label} has hidden RGB contamination under alpha: {asset_key}")
    if float(edge_metrics["greenBoundaryRatio"]) > max_green_risk:
        errors.append(f"{asset_label} has green-edge risk above threshold: {asset_key}")
    if float(edge_metrics["whiteFringeRiskRatio"]) > max_white_risk:
        errors.append(f"{asset_label} has white-fringe risk above threshold: {asset_key}")

    report["errors"] = list(errors)
    report["passed"] = not errors
    return report, errors


def _validate_sprite_sheet(
    *,
    asset_key: str,
    definition: dict[str, Any],
    assets: dict[str, str],
    theme_root: Path,
    referenced_sprites: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    sprites = definition.get("sprites", {})
    report: dict[str, Any] = {
        "layout": str(definition.get("layout", "")),
        "sprites": {},
        "passed": False,
    }
    if report["layout"] != "2x2-custom-clips":
        errors.append(f"pose sprite sheet layout must be 2x2-custom-clips: {asset_key}")
    if not isinstance(sprites, dict) or not sprites:
        errors.append(f"pose sprite sheet has no sprite declarations: {asset_key}")
        report["errors"] = errors
        return report, errors
    sprite_ids = {str(value) for value in sprites}
    if sprite_ids != set(referenced_sprites):
        missing = sorted(set(referenced_sprites) - sprite_ids)
        unused = sorted(sprite_ids - set(referenced_sprites))
        if missing:
            errors.append(f"pose sprite sheet declarations are missing: {asset_key}: {missing}")
        if unused:
            errors.append(f"pose sprite sheet declarations are not referenced: {asset_key}: {unused}")

    relative_value = assets.get(asset_key, "")
    resolved = (theme_root / relative_value).resolve() if relative_value else Path()
    try:
        resolved.relative_to(theme_root.resolve())
    except ValueError:
        errors.append(f"pose sprite sheet path escapes theme root: {asset_key}")
        report["errors"] = errors
        return report, errors
    if not resolved.is_file():
        errors.append(f"pose sprite sheet does not exist: {asset_key}")
        report["errors"] = errors
        return report, errors
    try:
        with Image.open(resolved) as source:
            source.load()
            if source.mode != "RGBA":
                errors.append(f"pose sprite sheet must be true RGBA: {asset_key}")
            sheet = source.copy() if source.mode == "RGBA" else source.convert("RGBA")
    except (OSError, ValueError) as exc:
        errors.append(f"pose sprite sheet cannot be decoded: {asset_key}: {exc}")
        report["errors"] = errors
        return report, errors

    sheet_width, sheet_height = sheet.size
    declared_size = _pair(definition.get("pixelSize"), (0, 0))
    if declared_size != (1230, 1278) or sheet.size != (1230, 1278):
        errors.append(
            f"v0.3 pose expansion sheet must remain 1230x1278: {asset_key}"
        )
    quality = definition.get("quality", {})
    quality = quality if isinstance(quality, dict) else {}
    min_transparent = max(0.10, _number(quality.get("minTransparentRatio"), 0.10))
    min_solid = max(0.05, _number(quality.get("minSolidRatio"), 0.05))
    min_corner = max(0.98, _number(quality.get("minTransparentCornerRatio"), 0.98))
    max_hidden = min(0.001, _number(quality.get("maxTransparentRgbLeakRatio"), 0.001))
    max_green = min(0.002, _number(quality.get("maxGreenBoundaryRatio"), 0.002))
    max_white = min(0.01, _number(quality.get("maxWhiteFringeRiskRatio"), 0.01))
    quadrants: set[str] = set()

    for raw_sprite_id, raw_sprite in sprites.items():
        sprite_id = str(raw_sprite_id)
        sprite_errors: list[str] = []
        sprite_report: dict[str, Any] = {"spriteId": sprite_id, "passed": False}
        if not isinstance(raw_sprite, dict):
            sprite_errors.append(f"pose sprite declaration must be an object: {asset_key}:{sprite_id}")
            report["sprites"][sprite_id] = {**sprite_report, "errors": sprite_errors}
            errors.extend(sprite_errors)
            continue
        pose_id = str(raw_sprite.get("poseId", ""))
        if referenced_sprites.get(sprite_id) != pose_id:
            sprite_errors.append(
                f"pose sprite poseId does not match its bundle: {asset_key}:{sprite_id}"
            )
        quadrant = str(raw_sprite.get("quadrant", ""))
        if quadrant not in _SPRITE_QUADRANTS:
            sprite_errors.append(f"pose sprite quadrant is invalid: {asset_key}:{sprite_id}")
        elif quadrant in quadrants:
            sprite_errors.append(f"pose sprite quadrant is duplicated: {asset_key}:{quadrant}")
        else:
            quadrants.add(quadrant)

        left, top, width, height = _quad(raw_sprite.get("sourceRect"), (0, 0, 0, 0))
        sprite_report["sourceRect"] = [left, top, width, height]
        if (
            left < 0
            or top < 0
            or width <= 0
            or height <= 0
            or left + width > sheet_width
            or top + height > sheet_height
        ):
            sprite_errors.append(f"pose sprite sourceRect is outside the sheet: {asset_key}:{sprite_id}")
            report["sprites"][sprite_id] = {**sprite_report, "errors": sprite_errors}
            errors.extend(sprite_errors)
            continue

        anchor = _normalized_pair(raw_sprite.get("anchor"))
        cord_anchor = _normalized_pair(raw_sprite.get("cordAnchor"))
        if anchor is None:
            sprite_errors.append(f"pose sprite anchor must be a normalized pair: {asset_key}:{sprite_id}")
        if cord_anchor is None:
            sprite_errors.append(
                f"pose sprite cordAnchor must be a normalized pair: {asset_key}:{sprite_id}"
            )
        click_mask = raw_sprite.get("clickMask", {})
        if not isinstance(click_mask, dict) or str(click_mask.get("type", "")) != "rect":
            click_mask = {}
            sprite_errors.append(f"pose sprite clickMask must be a rect: {asset_key}:{sprite_id}")
        hit_rect = _normalized_rect(click_mask.get("rect"))
        alpha_threshold = int(_number(click_mask.get("alphaThreshold"), -1))
        if hit_rect is None:
            sprite_errors.append(
                f"pose sprite clickMask.rect must be normalized: {asset_key}:{sprite_id}"
            )
        if alpha_threshold != _SPRITE_ALPHA_THRESHOLD:
            sprite_errors.append(
                f"pose sprite alphaThreshold must be {_SPRITE_ALPHA_THRESHOLD}: "
                f"{asset_key}:{sprite_id}"
            )

        crop = sheet.crop((left, top, left + width, top + height))
        alpha = crop.getchannel("A")
        alpha_payload = alpha.tobytes()
        pixel_count = max(1, width * height)
        transparent_ratio = sum(value == 0 for value in alpha_payload) / pixel_count
        solid_ratio = sum(value >= 192 for value in alpha_payload) / pixel_count
        corner_min, corner_values = _transparent_corner_ratio(alpha)
        edge_metrics = _edge_risk_metrics(crop)
        meaningful_bbox = _meaningful_alpha_bbox(alpha, _SPRITE_ALPHA_THRESHOLD)
        sprite_report.update(
            {
                "poseId": pose_id,
                "quadrant": quadrant,
                "anchor": list(anchor) if anchor else None,
                "cordAnchor": list(cord_anchor) if cord_anchor else None,
                "clickMask": {"type": "rect", "rect": list(hit_rect) if hit_rect else None},
                "aspectRatio": width / max(1, height),
                "transparentRatio": transparent_ratio,
                "solidRatio": solid_ratio,
                "cornerTransparentRatios": corner_values,
                "meaningfulAlphaBounds": list(meaningful_bbox) if meaningful_bbox else None,
                **edge_metrics,
            }
        )
        if meaningful_bbox is None:
            sprite_errors.append(f"pose sprite has no subject: {asset_key}:{sprite_id}")
        else:
            bbox_left, bbox_top, bbox_right, bbox_bottom = meaningful_bbox
            insets = (
                bbox_left,
                bbox_top,
                width - bbox_right,
                height - bbox_bottom,
            )
            minimum_inset = max(8, math.ceil(min(width, height) * 0.015))
            sprite_report["edgeInsets"] = list(insets)
            sprite_report["minimumEdgeInset"] = minimum_inset
            if min(insets) < minimum_inset:
                sprite_errors.append(
                    f"pose sprite subject touches its clip edge: {asset_key}:{sprite_id}"
                )
            subject_center_x = left + (bbox_left + bbox_right) / 2.0
            subject_center_y = top + (bbox_top + bbox_bottom) / 2.0
            expected_left = quadrant.endswith("left")
            expected_top = quadrant.startswith("top")
            in_expected_quadrant = (
                (subject_center_x < sheet_width / 2.0) == expected_left
                and (subject_center_y < sheet_height / 2.0) == expected_top
            )
            sprite_report["subjectCenter"] = [subject_center_x, subject_center_y]
            sprite_report["subjectInDeclaredQuadrant"] = in_expected_quadrant
            if not in_expected_quadrant:
                sprite_errors.append(
                    f"pose sprite subject is outside its declared quadrant: {asset_key}:{sprite_id}"
                )
            if hit_rect is not None:
                hit_left, hit_top, hit_width, hit_height = hit_rect
                hit_right = hit_left + hit_width
                hit_bottom = hit_top + hit_height
                normalized_bbox = (
                    bbox_left / width,
                    bbox_top / height,
                    bbox_right / width,
                    bbox_bottom / height,
                )
                sprite_report["normalizedSubjectBounds"] = list(normalized_bbox)
                if (
                    hit_left > normalized_bbox[0]
                    or hit_top > normalized_bbox[1]
                    or hit_right < normalized_bbox[2]
                    or hit_bottom < normalized_bbox[3]
                ):
                    sprite_errors.append(
                        f"pose sprite clickMask does not contain its subject: {asset_key}:{sprite_id}"
                    )
        if transparent_ratio < min_transparent:
            sprite_errors.append(f"pose sprite has too little transparency: {asset_key}:{sprite_id}")
        if solid_ratio < min_solid:
            sprite_errors.append(f"pose sprite has too little solid subject: {asset_key}:{sprite_id}")
        if corner_min < min_corner:
            sprite_errors.append(f"pose sprite corners are not transparent: {asset_key}:{sprite_id}")
        if float(edge_metrics["transparentRgbLeakRatio"]) > max_hidden:
            sprite_errors.append(f"pose sprite has hidden RGB matte: {asset_key}:{sprite_id}")
        if float(edge_metrics["greenBoundaryRatio"]) > max_green:
            sprite_errors.append(f"pose sprite has green-edge matte: {asset_key}:{sprite_id}")
        if float(edge_metrics["whiteFringeRiskRatio"]) > max_white:
            sprite_errors.append(f"pose sprite has white-edge matte: {asset_key}:{sprite_id}")
        sprite_report["errors"] = sprite_errors
        sprite_report["passed"] = not sprite_errors
        report["sprites"][sprite_id] = sprite_report
        errors.extend(sprite_errors)

    if quadrants != _SPRITE_QUADRANTS:
        errors.append(
            f"pose sprite sheet must cover all four quadrants exactly once: {asset_key}"
        )
    report["errors"] = errors
    report["passed"] = not errors and len(report["sprites"]) == 4
    return report, errors


def validate_pose_assets(theme_path: Path, qml_path: Path) -> dict[str, Any]:
    theme_path = theme_path.resolve()
    qml_path = qml_path.resolve()
    errors: list[str] = []
    try:
        manifest = json.loads(theme_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "schemaVersion": 1,
            "theme": str(theme_path),
            "qml": str(qml_path),
            "assets": {},
            "errors": [f"cannot read theme manifest: {exc}"],
            "passed": False,
        }

    character = manifest.get("character", {})
    if not isinstance(character, dict):
        character = {}
        errors.append("character must be an object")
    gate_version = character.get("poseAssetGateVersion")
    if gate_version != 3:
        errors.append("character.poseAssetGateVersion must be 3")
    specs = character.get("poseArtworkSpecs", {})
    if not isinstance(specs, dict) or not specs:
        specs = {}
        errors.append("character.poseArtworkSpecs must be a non-empty object")
    assets = manifest.get("assets", {})
    if not isinstance(assets, dict):
        assets = {}
        errors.append("assets must be an object")
    assets = {str(key): str(value) for key, value in assets.items()}

    bundles = character.get("poseBundles", {})
    if not isinstance(bundles, dict):
        bundles = {}
        errors.append("character.poseBundles must be an object")
    declared_artwork: set[str] = set()
    referenced_sprites: dict[str, dict[str, str]] = {}
    for pose_id, raw_definition in bundles.items():
        if not isinstance(raw_definition, dict):
            continue
        if str(raw_definition.get("recipe", "")) != "pose-artwork":
            continue
        asset_key = str(raw_definition.get("artworkAsset", ""))
        if not asset_key:
            errors.append(f"pose-artwork bundle lacks artworkAsset: {pose_id}")
            continue
        declared_artwork.add(asset_key)
        if asset_key not in specs:
            errors.append(f"pose-artwork bundle lacks artwork spec: {pose_id} -> {asset_key}")
            continue
        specification = specs.get(asset_key)
        sprites = specification.get("sprites") if isinstance(specification, dict) else None
        sprite_id = str(raw_definition.get("spriteId", ""))
        if isinstance(sprites, dict) and sprites:
            if not sprite_id:
                errors.append(f"pose sprite bundle lacks spriteId: {pose_id} -> {asset_key}")
                continue
            previous_pose = referenced_sprites.setdefault(asset_key, {}).get(sprite_id)
            if previous_pose is not None:
                errors.append(
                    f"pose sprite is linked by more than one bundle: "
                    f"{asset_key}:{sprite_id} -> {previous_pose}, {pose_id}"
                )
            referenced_sprites[asset_key][sprite_id] = str(pose_id)
        elif sprite_id:
            errors.append(f"non-sheet pose bundle cannot declare spriteId: {pose_id}")

    spec_keys = {str(key) for key in specs}
    optional_spec_keys = {
        str(key)
        for key, value in specs.items()
        if isinstance(value, dict) and bool(value.get("optional"))
    }
    for raw_asset_key, raw_specification in specs.items():
        asset_key = str(raw_asset_key)
        if not isinstance(raw_specification, dict):
            errors.append(f"pose artwork spec must be an object: {asset_key}")
            continue
        sprites = raw_specification.get("sprites")
        if isinstance(sprites, dict) and sprites:
            mask_items = (
                (f"{asset_key}:{sprite_id}", sprite.get("clickMask"))
                for sprite_id, sprite in sprites.items()
                if isinstance(sprite, dict)
            )
        else:
            mask_items = ((asset_key, raw_specification.get("clickMask")),)
        for mask_label, click_mask in mask_items:
            try:
                validate_pose_click_mask(
                    click_mask,
                    label=f"pose artwork clickMask: {mask_label}",
                )
            except ValueError as exc:
                errors.append(str(exc))
    required_spec_keys = spec_keys - optional_spec_keys
    if declared_artwork != required_spec_keys:
        missing = sorted(declared_artwork - required_spec_keys)
        unused = sorted(required_spec_keys - declared_artwork)
        if missing:
            errors.append(f"pose artwork specs missing keys: {missing}")
        if unused:
            errors.append(f"pose artwork specs are not linked by a bundle: {unused}")
    habitat_variants = character.get("habitatPoseVariants", {})
    if not isinstance(habitat_variants, dict):
        habitat_variants = {}
        errors.append("character.habitatPoseVariants must be an object")
    optional_references: dict[str, list[str]] = {}
    declared_outfits = {
        str(value) for value in character.get("outfits", [])
    }
    for raw_variant_id, raw_definition in habitat_variants.items():
        if not isinstance(raw_definition, dict):
            continue
        variant_id = str(raw_variant_id)
        asset_key = str(raw_definition.get("optionalArtworkAsset", ""))
        enabled = bool(raw_definition.get("optionalArtworkEnabled", False))
        if enabled and not asset_key:
            errors.append(
                f"enabled optional habitat artwork has no asset key: {variant_id}"
            )
            continue
        if not asset_key:
            continue
        if asset_key not in optional_spec_keys:
            errors.append(
                f"habitat variant references unknown optional artwork spec: "
                f"{variant_id} -> {asset_key}"
            )
            continue
        optional_references.setdefault(asset_key, []).append(variant_id)
        if asset_key not in assets:
            errors.append(
                f"habitat variant references unmapped optional artwork asset: "
                f"{variant_id} -> {asset_key}"
            )
        artwork_outfits = raw_definition.get("artworkOutfits")
        if not isinstance(artwork_outfits, list) or not artwork_outfits:
            errors.append(
                f"optional habitat artwork must declare artworkOutfits: {variant_id}"
            )
        else:
            normalized_outfits = tuple(str(value) for value in artwork_outfits)
            if "*" in normalized_outfits and normalized_outfits != ("*",):
                errors.append(
                    f"optional habitat artwork wildcard must be the only outfit: "
                    f"{variant_id}"
                )
            unknown_outfits = {
                value for value in normalized_outfits
                if value != "*" and value not in declared_outfits
            }
            if unknown_outfits:
                errors.append(
                    f"optional habitat artwork declares unknown outfits: {variant_id}"
                )
        if enabled:
            relative_value = assets.get(asset_key, "")
            candidate = (
                (theme_path.parent / relative_value).resolve()
                if relative_value else None
            )
            if candidate is None or not candidate.is_file():
                errors.append(
                    f"enabled optional habitat artwork file is missing: "
                    f"{variant_id} -> {asset_key}"
                )
    unreferenced_optional = sorted(optional_spec_keys - set(optional_references))
    if unreferenced_optional:
        errors.append(
            f"optional pose artwork specs are not linked by a habitat variant: "
            f"{unreferenced_optional}"
        )
    runtime_pose_keys = {key for key in assets if re.fullmatch(r"pose[A-Z][A-Za-z0-9]*", key)}
    if runtime_pose_keys != spec_keys:
        missing = sorted(runtime_pose_keys - spec_keys)
        stale = sorted(spec_keys - runtime_pose_keys)
        if missing:
            errors.append(f"runtime pose assets lack quality specs: {missing}")
        if stale:
            errors.append(f"quality specs reference missing runtime assets: {stale}")

    qml_ratios, qml_errors = _parse_qml_ratios(qml_path)
    errors.extend(qml_errors)
    qml_keys = set(qml_ratios)
    sheet_keys = {
        str(key)
        for key, value in specs.items()
        if isinstance(value, dict) and isinstance(value.get("sprites"), dict)
        and bool(value.get("sprites"))
    }
    expected_qml_keys = spec_keys - sheet_keys
    if qml_keys != expected_qml_keys:
        missing = sorted(expected_qml_keys - qml_keys)
        stale = sorted(qml_keys - expected_qml_keys)
        if missing:
            errors.append(f"QML aspect ratios missing pose keys: {missing}")
        if stale:
            errors.append(f"QML aspect ratios have undeclared pose keys: {stale}")

    asset_reports: dict[str, Any] = {}
    for asset_key in sorted(spec_keys):
        raw_definition = specs.get(asset_key)
        if not isinstance(raw_definition, dict):
            errors.append(f"pose artwork spec must be an object: {asset_key}")
            continue
        if asset_key in optional_spec_keys:
            declaration, declaration_errors, resolved = _validate_optional_declaration(
                asset_key=asset_key,
                definition=raw_definition,
                assets=assets,
                theme_root=theme_path.parent,
                qml_ratio=qml_ratios.get(asset_key),
            )
            declaration["habitatVariants"] = sorted(optional_references.get(asset_key, []))
            if declaration_errors or resolved is None or not resolved.is_file():
                asset_reports[asset_key] = declaration
                errors.extend(declaration_errors)
                continue
            report, asset_errors = _validate_asset(
                asset_key=asset_key,
                definition=raw_definition,
                assets=assets,
                theme_root=theme_path.parent,
                qml_ratio=qml_ratios.get(asset_key),
                asset_label="optional pose",
                legacy_baselines={},
                requires_qml_ratio=True,
            )
            report.update(
                {
                    "optional": True,
                    "validated": True,
                    "status": "verified",
                    "habitatVariants": sorted(optional_references.get(asset_key, [])),
                }
            )
            silhouette_gate = raw_definition.get("silhouetteGate", {})
            silhouette_gate = silhouette_gate if isinstance(silhouette_gate, dict) else {}
            baseline_key = str(silhouette_gate.get("distinctFromAsset", ""))
            baseline_value = assets.get(baseline_key, "")
            baseline_path = (
                (theme_path.parent / baseline_value).resolve()
                if baseline_value else Path()
            )
            if not baseline_path.is_file():
                asset_errors.append(
                    f"optional pose silhouette baseline does not exist: "
                    f"{asset_key} -> {baseline_key}"
                )
            else:
                metrics = _silhouette_distinctness(resolved, baseline_path)
                maximum_iou = _number(
                    silhouette_gate.get("maxIntersectionOverUnion"), 0.82
                )
                minimum_difference = _number(
                    silhouette_gate.get("minDifferenceRatio"), 0.18
                )
                metrics.update(
                    {
                        "baselineAsset": baseline_key,
                        "maxIntersectionOverUnion": maximum_iou,
                        "minDifferenceRatio": minimum_difference,
                    }
                )
                report["silhouetteDistinctness"] = metrics
                if float(metrics["intersectionOverUnion"]) > maximum_iou:
                    asset_errors.append(
                        f"optional pose silhouette is too similar to baseline: "
                        f"{asset_key} -> {baseline_key}"
                    )
                if float(metrics["differenceRatio"]) < minimum_difference:
                    asset_errors.append(
                        f"optional pose silhouette difference is below threshold: "
                        f"{asset_key} -> {baseline_key}"
                    )
            report["errors"] = list(asset_errors)
            report["passed"] = not asset_errors
            asset_reports[asset_key] = report
            errors.extend(asset_errors)
            continue
        report, asset_errors = _validate_asset(
            asset_key=asset_key,
            definition=raw_definition,
            assets=assets,
            theme_root=theme_path.parent,
            qml_ratio=qml_ratios.get(asset_key),
            asset_label="pose",
            legacy_baselines=_LEGACY_POSE_BASELINES,
            requires_qml_ratio=asset_key not in sheet_keys,
        )
        if asset_key in sheet_keys:
            sprite_report, sprite_errors = _validate_sprite_sheet(
                asset_key=asset_key,
                definition=raw_definition,
                assets=assets,
                theme_root=theme_path.parent,
                referenced_sprites=referenced_sprites.get(asset_key, {}),
            )
            report["spriteSheet"] = sprite_report
            report["passed"] = bool(report["passed"]) and bool(sprite_report["passed"])
            asset_errors.extend(sprite_errors)
        asset_reports[asset_key] = report
        errors.extend(asset_errors)

    try:
        qml_source = qml_path.read_text(encoding="utf-8")
    except OSError:
        qml_source = ""
    required_mask_contract = {
        "poseArtworkClickMask": "manifest click-mask lookup",
        "containsDeclaredMask": "manifest click-mask evaluation",
    }
    for token, label in required_mask_contract.items():
        if token not in qml_source:
            errors.append(f"V03PetBody.qml lacks {label}: {token}")
    mirror_tokens = (
        "poseArtworkFrame.displayedMirror",
        "poseArtworkFrame.slotMirror(slot)",
    )
    if not any(token in qml_source for token in mirror_tokens):
        errors.append(
            "V03PetBody.qml lacks source-coordinate mirror handling: "
            + " or ".join(mirror_tokens)
        )

    if sheet_keys:
        required_qml_contract = {
            "poseArtworkClipRect": "runtime sprite clip property",
            "poseArtworkSpriteDefinition": "manifest sprite definition lookup",
        }
        for token, label in required_qml_contract.items():
            if token not in qml_source:
                errors.append(f"V03PetBody.qml lacks {label}: {token}")
        clipping_contracts = (
            (
                "sourceClipRect: poseArtworkFrame.displayedClipRect",
                "sourceClipRect: poseArtworkFrame.outgoingClipRect",
            ),
            (
                "sourceClipRect: poseArtworkFrame.slotAClipRect",
                "sourceClipRect: poseArtworkFrame.slotBClipRect",
            ),
        )
        if not any(all(token in qml_source for token in contract)
                   for contract in clipping_contracts):
            errors.append(
                "V03PetBody.qml lacks dual-slot sprite clipping contract"
            )

    return {
        "schemaVersion": 3,
        "theme": str(theme_path),
        "qml": str(qml_path),
        "gateVersion": gate_version,
        "runtimeArtworkKeys": sorted(declared_artwork),
        "optionalArtworkKeys": sorted(optional_spec_keys),
        "qmlArtworkKeys": sorted(qml_keys),
        "spriteSheetKeys": sorted(sheet_keys),
        "assets": asset_reports,
        "errors": errors,
        "passed": not errors and bool(asset_reports),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production Lilies pose artwork")
    parser.add_argument("--theme", type=Path, default=DEFAULT_THEME)
    parser.add_argument("--qml", type=Path, default=DEFAULT_QML)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    report = validate_pose_assets(args.theme, args.qml)
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
