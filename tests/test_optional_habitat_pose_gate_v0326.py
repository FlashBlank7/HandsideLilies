from __future__ import annotations

import json
from pathlib import Path
import shutil

from PIL import Image, ImageDraw

from scripts.verify_pose_assets import (
    _silhouette_distinctness,
    validate_pose_assets,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy_first_encounter_theme(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    source_manifest_path = (
        PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
    )
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    theme_root = tmp_path / "theme"
    (theme_root / "assets").mkdir(parents=True)
    for asset_key, specification in manifest["character"]["poseArtworkSpecs"].items():
        if specification.get("optional"):
            continue
        relative = Path(manifest["assets"][asset_key])
        shutil.copyfile(source_manifest_path.parent / relative, theme_root / relative)
    (theme_root / "theme.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return theme_root, manifest


def _micro_corner_subject(path: Path) -> None:
    size = 1536
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (0.25 * size, 0.10 * size, 0.70 * size, 0.40 * size),
        fill=(80, 74, 70, 255),
    )
    draw.polygon(
        (
            (0.30 * size, 0.38 * size),
            (0.62 * size, 0.38 * size),
            (0.72 * size, 0.82 * size),
            (0.30 * size, 0.86 * size),
        ),
        fill=(80, 74, 70, 255),
    )
    draw.ellipse(
        (0.16 * size, 0.40 * size, 0.28 * size, 0.62 * size),
        fill=(80, 74, 70, 255),
    )
    image.save(path)


def _shape(path: Path, *, horizontal: bool = False, mirrored: bool = False) -> None:
    image = Image.new("RGBA", (160, 240), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if horizontal:
        draw.rounded_rectangle((15, 92, 145, 154), radius=26, fill=(80, 74, 70, 255))
    else:
        draw.ellipse((43, 18, 117, 92), fill=(80, 74, 70, 255))
        draw.polygon(((57, 82), (103, 82), (128, 222), (32, 222)), fill=(80, 74, 70, 255))
        draw.rounded_rectangle((100, 98, 151, 126), radius=10, fill=(80, 74, 70, 255))
    if mirrored:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    image.save(path)


def test_silhouette_metric_catches_copies_and_mirrors_but_separates_real_pose(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.png"
    clone = tmp_path / "clone.png"
    mirrored = tmp_path / "mirrored.png"
    distinct = tmp_path / "distinct.png"
    _shape(baseline)
    shutil.copyfile(baseline, clone)
    _shape(mirrored, mirrored=True)
    _shape(distinct, horizontal=True)

    clone_metrics = _silhouette_distinctness(clone, baseline)
    mirror_metrics = _silhouette_distinctness(mirrored, baseline)
    distinct_metrics = _silhouette_distinctness(distinct, baseline)

    assert clone_metrics["intersectionOverUnion"] == 1.0
    assert clone_metrics["differenceRatio"] == 0.0
    assert mirror_metrics["intersectionOverUnion"] > 0.99
    assert mirror_metrics["matchedMirroredBaseline"] is True
    assert distinct_metrics["intersectionOverUnion"] < 0.82
    assert distinct_metrics["differenceRatio"] > 0.18


def test_optional_asset_present_but_reusing_fallback_silhouette_fails_full_gate(
    tmp_path: Path,
) -> None:
    source_manifest_path = (
        PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
    )
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    theme_root = tmp_path / "theme"
    assets_root = theme_root / "assets"
    assets_root.mkdir(parents=True)
    for asset_key, specification in manifest["character"]["poseArtworkSpecs"].items():
        if specification.get("optional"):
            continue
        relative = Path(manifest["assets"][asset_key])
        shutil.copyfile(source_manifest_path.parent / relative, theme_root / relative)

    baseline_path = theme_root / manifest["assets"]["posePerchProne"]
    with Image.open(baseline_path) as source:
        source.load()
        clone = source.copy().resize((1000, 997), Image.Resampling.NEAREST)
    candidate = Image.new("RGBA", (1280, 1536), (0, 0, 0, 0))
    candidate.alpha_composite(clone, (140, 270))
    candidate.save(theme_root / manifest["assets"]["poseWindowDangleV1"])
    manifest_path = theme_root / "theme.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = validate_pose_assets(
        manifest_path,
        PROJECT_ROOT / "qml" / "V03PetBody.qml",
    )

    assert report["passed"] is False
    optional = report["assets"]["poseWindowDangleV1"]
    assert optional["validated"] is True
    assert optional["status"] == "verified"
    assert optional["silhouetteDistinctness"]["intersectionOverUnion"] > 0.82
    assert optional["passed"] is False
    errors = "\n".join(report["errors"])
    assert "silhouette is too similar to baseline" in errors
    assert "silhouette difference is below threshold" in errors


def test_micro_corner_asset_gets_full_alpha_mask_and_edge_peek_distinctness_gate(
    tmp_path: Path,
) -> None:
    theme_root, manifest = _copy_first_encounter_theme(tmp_path)
    candidate = theme_root / manifest["assets"]["poseMicroCornerGripV1"]
    _micro_corner_subject(candidate)

    report = validate_pose_assets(
        theme_root / "theme.json",
        PROJECT_ROOT / "qml" / "V03PetBody.qml",
    )

    assert report["passed"] is True, "\n".join(report["errors"])
    micro = report["assets"]["poseMicroCornerGripV1"]
    assert micro["validated"] is True
    assert micro["status"] == "verified"
    assert micro["format"] == "PNG"
    assert micro["mode"] == "RGBA"
    assert micro["bands"] == ["R", "G", "B", "A"]
    assert micro["pixelSize"] == [1536, 1536]
    assert micro["alphaExtrema"] == [0, 255]
    assert micro["meaningfulAlphaBounds"] is not None
    assert min(micro["cornerTransparentRatios"]) >= 0.98
    relation = micro["clickMaskRelation"]
    assert relation["subjectCoverage"] >= relation["minimumSubjectCoverage"]
    assert relation["subjectPrecision"] >= relation["minimumSubjectPrecision"]
    silhouette = micro["silhouetteDistinctness"]
    assert silhouette["baselineAsset"] == "poseEdgePeek"
    assert silhouette["intersectionOverUnion"] <= silhouette[
        "maxIntersectionOverUnion"
    ]
    assert silhouette["differenceRatio"] >= silhouette["minDifferenceRatio"]


def test_micro_corner_cannot_reuse_edge_peek_silhouette_even_on_square_canvas(
    tmp_path: Path,
) -> None:
    theme_root, manifest = _copy_first_encounter_theme(tmp_path)
    edge_path = theme_root / manifest["assets"]["poseEdgePeek"]
    candidate_path = theme_root / manifest["assets"]["poseMicroCornerGripV1"]
    with Image.open(edge_path) as source:
        source.load()
        clone = source.copy().resize((450, 1229), Image.Resampling.NEAREST)
    candidate = Image.new("RGBA", (1536, 1536), (0, 0, 0, 0))
    candidate.alpha_composite(clone, ((1536 - 450) // 2, (1536 - 1229) // 2))
    candidate.save(candidate_path)

    report = validate_pose_assets(
        theme_root / "theme.json",
        PROJECT_ROOT / "qml" / "V03PetBody.qml",
    )

    assert report["passed"] is False
    micro = report["assets"]["poseMicroCornerGripV1"]
    silhouette = micro["silhouetteDistinctness"]
    assert silhouette["baselineAsset"] == "poseEdgePeek"
    assert silhouette["intersectionOverUnion"] > silhouette[
        "maxIntersectionOverUnion"
    ]
    assert silhouette["differenceRatio"] < silhouette["minDifferenceRatio"]
    errors = "\n".join(report["errors"])
    assert "poseMicroCornerGripV1 -> poseEdgePeek" in errors
