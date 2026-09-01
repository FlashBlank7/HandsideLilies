from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.verify_pose_assets import validate_pose_assets


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_qml(path: Path, *, width: int, height: int) -> None:
    path.write_text(
        "\n".join(
            (
                "import QtQuick",
                "Item {",
                "    readonly property var poseArtworkClickMask: ({})",
                "    function containsDeclaredMask(nx, ny, mask) { return false }",
                "    // poseArtworkFrame.displayedMirror",
                "    readonly property real poseArtworkAspectRatio: {",
                "        switch (poseArtworkKey) {",
                f'        case "poseDemo": return {width}.0 / {height}.0',
                "        default: return 1.0",
                "        }",
                "    }",
                "    readonly property bool finished: true",
                "}",
            )
        ),
        encoding="utf-8",
    )


def _write_manifest(
    root: Path,
    *,
    asset_path: str = "assets/demo.png",
    pixel_size: tuple[int, int] = (64, 96),
    minimum_size: tuple[int, int] = (48, 72),
    quality: dict[str, float] | None = None,
    artwork_asset: str = "poseDemo",
    resolution_tier: str = "production-v1",
) -> Path:
    manifest = root / "theme.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "gate-fixture",
                "version": "1",
                "title": "fixture",
                "renderers": ["scene2d"],
                "defaultRenderer": "scene2d",
                "character": {
                    "poseAssetGateVersion": 3,
                    "poses": ["demo"],
                    "poseBundles": {
                        "demo": {
                            "recipe": "pose-artwork",
                            "artworkAsset": artwork_asset,
                            "clickMask": "character",
                            "compatibleOutfits": ["*"],
                        }
                    },
                    "poseArtworkSpecs": {
                        "poseDemo": {
                            "resolutionTier": resolution_tier,
                            "pixelSize": list(pixel_size),
                            "minimumSize": list(minimum_size),
                            "aspectRatio": pixel_size[0] / pixel_size[1],
                            "clickMask": {
                                "type": "composite",
                                "ellipses": [[0.5, 0.25, 0.35, 0.2]],
                                "polygons": [
                                    [[0.3, 0.35], [0.7, 0.35], [0.75, 0.9], [0.25, 0.9]]
                                ],
                            },
                            "quality": quality
                            or {
                                "minTransparentRatio": 0.15,
                                "minSolidRatio": 0.05,
                                "minTransparentCornerRatio": 1.0,
                                "maxTransparentRgbLeakRatio": 0.001,
                                "maxGreenBoundaryRatio": 0.001,
                                "maxWhiteFringeRiskRatio": 0.001,
                            },
                        }
                    },
                },
                "assets": {"poseDemo": asset_path},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


def _valid_rgba(size: tuple[int, int] = (64, 96)) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (size[0] // 4, size[1] // 5, size[0] * 3 // 4, size[1] * 4 // 5),
        fill=(78, 72, 68, 255),
    )
    return image


def _error_text(report: dict[str, object]) -> str:
    return "\n".join(str(value) for value in report["errors"])


def test_first_encounter_formal_pose_assets_pass_the_gate() -> None:
    report = validate_pose_assets(
        PROJECT_ROOT / "themes" / "first-encounter" / "theme.json",
        PROJECT_ROOT / "qml" / "V03PetBody.qml",
    )
    assert report["passed"] is True, _error_text(report)
    assert report["runtimeArtworkKeys"] == [
        "poseEdgePeek",
        "poseExpansionSheet",
        "poseFocusKneel",
        "poseListeningLive",
        "posePerchProne",
        "poseTitleSit",
    ]
    assert report["optionalArtworkKeys"] == [
        "poseEdgeLeanV1",
        "poseMicroCornerGripV1",
        "poseWideWindowSprawlV1",
        "poseWindowDangleV1",
        "poseWindowProneV2",
    ]
    for key in report["optionalArtworkKeys"]:
        optional = report["assets"][key]
        assert optional["passed"] is True
        assert optional["optional"] is True
        assert optional["validated"] is False
        assert optional["status"] == "dormant-missing"
    legacy_keys = {
        "poseEdgePeek",
        "poseListeningLive",
        "posePerchProne",
        "poseTitleSit",
    }
    for key in legacy_keys:
        asset = report["assets"][key]
        assert asset["format"] == "PNG"
        assert asset["resolutionTier"] == "legacy-v1"
        assert asset["mode"] == "RGBA"
        assert asset["bands"] == ["R", "G", "B", "A"]
        assert asset["alphaExtrema"] == [0, 255]
        assert asset["cornerTransparentRatios"] == [1.0, 1.0, 1.0, 1.0]
        assert asset["transparentRgbLeakRatio"] == 0.0
        assert asset["greenBoundaryRatio"] == 0.0
        assert asset["pixelSize"] == asset["declaredPixelSize"]
        assert asset["qmlAspectRatio"]["numerator"] == asset["pixelSize"][0]
        assert asset["qmlAspectRatio"]["denominator"] == asset["pixelSize"][1]
        assert asset["meaningfulAlphaBounds"] is not None
        assert asset["clickMaskRelation"]["subjectCoverage"] >= 0.90
        assert asset["clickMaskRelation"]["subjectPrecision"] >= 0.20
    sheet = report["assets"]["poseExpansionSheet"]
    assert sheet["format"] == "PNG"
    assert sheet["mode"] == "RGBA"
    assert sheet["pixelSize"] == [1230, 1278]
    assert sheet["sha256"] == sheet["declaredSha256"]
    assert sheet["qmlAspectRatio"] is None
    focus = report["assets"]["poseFocusKneel"]
    assert focus["resolutionTier"] == "production-v1"
    assert focus["pixelSize"] == [1145, 1374]
    assert focus["sha256"] == focus["declaredSha256"]
    assert focus["qmlAspectRatio"] == {
        "numerator": 1145.0,
        "denominator": 1374.0,
        "ratio": 1145 / 1374,
    }
    assert focus["meaningfulAlphaBounds"] is not None
    assert focus["clickMaskRelation"]["subjectCoverage"] >= 0.90
    assert focus["clickMaskRelation"]["subjectPrecision"] >= 0.20
    sprite_sheet = sheet["spriteSheet"]
    assert sprite_sheet["passed"] is True
    assert set(sprite_sheet["sprites"]) == {
        "reading", "presenting", "box-support", "resting"
    }
    assert {value["quadrant"] for value in sprite_sheet["sprites"].values()} == {
        "top-left", "top-right", "bottom-left", "bottom-right"
    }
    for sprite in sprite_sheet["sprites"].values():
        assert sprite["passed"] is True
        assert min(sprite["edgeInsets"]) >= sprite["minimumEdgeInset"]
        assert sprite["transparentRgbLeakRatio"] == 0.0
        assert sprite["subjectInDeclaredQuadrant"] is True


def test_gate_rejects_missing_or_invalid_standalone_click_masks(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba().save(assets / "demo.png")
    manifest = _write_manifest(theme_root)
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["character"]["poseArtworkSpecs"]["poseDemo"].pop("clickMask")
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    missing = validate_pose_assets(manifest, qml)
    assert missing["passed"] is False
    assert "pose artwork clickMask: poseDemo must be an object" in _error_text(missing)

    raw["character"]["poseArtworkSpecs"]["poseDemo"]["clickMask"] = {
        "type": "composite",
        "rects": [[0.8, 0.1, 0.3, 0.5]],
    }
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    outside = validate_pose_assets(manifest, qml)
    assert outside["passed"] is False
    assert "must stay inside normalized artwork bounds" in _error_text(outside)

    raw["character"]["poseArtworkSpecs"]["poseDemo"]["clickMask"] = {
        "type": "composite",
        "polygons": [[[0.2, 0.2], [0.8, 0.8]]],
    }
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    polygon = validate_pose_assets(manifest, qml)
    assert polygon["passed"] is False
    assert "must contain at least three normalized points" in _error_text(polygon)


def test_gate_rejects_empty_alpha_bbox_and_click_mask_disconnected_from_subject(
    tmp_path: Path,
) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    manifest = _write_manifest(theme_root)
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    Image.new("RGBA", (64, 96), (0, 0, 0, 0)).save(assets / "demo.png")
    empty = validate_pose_assets(manifest, qml)
    assert empty["passed"] is False
    assert empty["assets"]["poseDemo"]["meaningfulAlphaBounds"] is None
    assert "has no meaningful alpha subject" in _error_text(empty)

    _valid_rgba().save(assets / "demo.png")
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["character"]["poseArtworkSpecs"]["poseDemo"]["clickMask"] = {
        "type": "rect",
        "rect": [0.80, 0.02, 0.15, 0.15],
    }
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    disconnected = validate_pose_assets(manifest, qml)
    relation = disconnected["assets"]["poseDemo"]["clickMaskRelation"]
    assert disconnected["passed"] is False
    assert relation["subjectCoverage"] == 0.0
    assert relation["subjectPrecision"] == 0.0
    errors = _error_text(disconnected)
    assert "clickMask covers too little meaningful alpha" in errors
    assert "clickMask has too little overlap with meaningful alpha" in errors


def test_gate_rejects_disabled_dangling_optional_artwork_reference(
    tmp_path: Path,
) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba().save(assets / "demo.png")
    manifest = _write_manifest(theme_root)
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["character"]["habitatPoseVariants"] = {
        "dormant-broken": {
            "optionalArtworkAsset": "poseMissing",
            "optionalArtworkEnabled": False,
            "artworkOutfits": ["*"],
        }
    }
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    report = validate_pose_assets(manifest, qml)

    assert report["passed"] is False
    assert (
        "habitat variant references unknown optional artwork spec: "
        "dormant-broken -> poseMissing"
    ) in _error_text(report)


def test_gate_rejects_rgb_and_baked_checkerboard(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    checker = Image.new("RGB", (64, 96))
    pixels = checker.load()
    for y in range(96):
        for x in range(64):
            value = 218 if ((x // 8) + (y // 8)) % 2 else 174
            pixels[x, y] = (value, value, value)
    checker.save(assets / "demo.png")
    manifest = _write_manifest(theme_root)
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    report = validate_pose_assets(manifest, qml)

    assert report["passed"] is False
    errors = _error_text(report)
    assert "must be true RGBA" in errors
    assert "alpha must contain both 0 and 255" in errors
    assert "too little real transparency" in errors
    assert "corners are not transparent enough" in errors


def test_gate_rejects_hidden_rgb_and_green_or_white_matte_edges(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    image = Image.new("RGBA", (64, 96), (245, 245, 245, 0))
    pixels = image.load()
    for y in range(24, 72):
        for x in range(18, 46):
            pixels[x, y] = (45, 42, 40, 255)
    for y in range(23, 73):
        for x in (17, 46):
            pixels[x, y] = (30, 220, 45, 160)
    for x in range(18, 46):
        pixels[x, 23] = (255, 255, 255, 128)
        pixels[x, 72] = (255, 255, 255, 128)
    image.save(assets / "demo.png")
    quality = {
        "minTransparentRatio": 0.15,
        "minSolidRatio": 0.05,
        "minTransparentCornerRatio": 1.0,
        "maxTransparentRgbLeakRatio": 0.0,
        "maxGreenBoundaryRatio": 0.0,
        "maxWhiteFringeRiskRatio": 0.0,
    }
    manifest = _write_manifest(theme_root, quality=quality)
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    report = validate_pose_assets(manifest, qml)

    errors = _error_text(report)
    assert report["passed"] is False
    assert "hidden RGB contamination under alpha" in errors
    assert "green-edge risk" in errors
    assert "white-fringe risk" in errors


def test_gate_rejects_low_resolution_and_qml_ratio_drift(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba().save(assets / "demo.png")
    manifest = _write_manifest(theme_root, minimum_size=(96, 128))
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=64)

    report = validate_pose_assets(manifest, qml)

    errors = _error_text(report)
    assert report["passed"] is False
    assert "below minimumSize" in errors
    assert "production-v1 minimumSize is below" in errors
    assert "QML aspect ratio does not match pixels" in errors
    assert "QML aspect ratio operands must equal pixelSize" not in errors


def test_gate_rejects_pixel_size_drift_even_when_all_ratios_are_equivalent(
    tmp_path: Path,
) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba().save(assets / "demo.png")
    manifest = _write_manifest(theme_root, pixel_size=(128, 192))
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=32, height=48)

    report = validate_pose_assets(manifest, qml)

    errors = _error_text(report)
    assert report["passed"] is False
    assert "pose pixelSize drift" in errors
    assert "QML aspect ratio operands must equal pixelSize" in errors


def test_production_tier_accepts_a_true_512_by_1024_minimum(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba((512, 1024)).save(assets / "demo.png")
    manifest = _write_manifest(
        theme_root,
        pixel_size=(512, 1024),
        minimum_size=(512, 1024),
    )
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=512, height=1024)

    report = validate_pose_assets(manifest, qml)

    assert report["passed"] is True, _error_text(report)
    assert report["assets"]["poseDemo"]["resolutionTier"] == "production-v1"


def test_new_pose_cannot_claim_the_legacy_resolution_tier(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba().save(assets / "demo.png")
    manifest = _write_manifest(theme_root, resolution_tier="legacy-v1")
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    report = validate_pose_assets(manifest, qml)

    assert report["passed"] is False
    assert "new pose assets cannot claim the legacy-v1 tier" in _error_text(report)


def test_gate_rejects_concept_paths_even_when_the_png_is_valid_rgba(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba().save(assets / "lilith-pose-concept-v1.png")
    manifest = _write_manifest(
        theme_root,
        asset_path="assets/lilith-pose-concept-v1.png",
    )
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    report = validate_pose_assets(manifest, qml)

    assert report["passed"] is False
    assert "concept/generated/chroma artwork cannot be a runtime pose" in _error_text(report)


def test_gate_rejects_paths_outside_the_theme_without_reading_them(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    theme_root.mkdir()
    _valid_rgba().save(tmp_path / "outside.png")
    manifest = _write_manifest(theme_root, asset_path="../outside.png")
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    report = validate_pose_assets(manifest, qml)

    asset = report["assets"]["poseDemo"]
    assert report["passed"] is False
    assert "pose asset path escapes theme root" in _error_text(report)
    assert "format" not in asset


def test_gate_policy_cannot_be_weakened_by_manifest_thresholds(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba().save(assets / "demo.png")
    quality = {
        "minTransparentRatio": 0.0,
        "minSolidRatio": 0.0,
        "minTransparentCornerRatio": 0.0,
        "maxTransparentRgbLeakRatio": 1.0,
        "maxGreenBoundaryRatio": 1.0,
        "maxWhiteFringeRiskRatio": 1.0,
    }
    manifest = _write_manifest(theme_root, quality=quality)
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    report = validate_pose_assets(manifest, qml)

    assert report["passed"] is False
    errors = _error_text(report)
    assert errors.count("outside gate policy") == 6


def test_gate_requires_every_pose_artwork_bundle_to_link_a_spec(tmp_path: Path) -> None:
    theme_root = tmp_path / "theme"
    assets = theme_root / "assets"
    assets.mkdir(parents=True)
    _valid_rgba().save(assets / "demo.png")
    manifest = _write_manifest(theme_root, artwork_asset="poseMissing")
    qml = tmp_path / "Pet.qml"
    _write_qml(qml, width=64, height=96)

    report = validate_pose_assets(manifest, qml)

    errors = _error_text(report)
    assert report["passed"] is False
    assert "pose-artwork bundle lacks artwork spec" in errors
    assert "pose artwork specs are not linked by a bundle" in errors


def test_expansion_sheet_gate_rejects_mechanical_quadrants_that_cut_subjects(
    tmp_path: Path,
) -> None:
    source_theme = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
    source_manifest = json.loads(source_theme.read_text(encoding="utf-8"))
    source_character = source_manifest["character"]
    theme_root = tmp_path / "theme"
    assets_root = theme_root / "assets"
    assets_root.mkdir(parents=True)
    shutil.copyfile(
        source_theme.parent / source_manifest["assets"]["poseExpansionSheet"],
        assets_root / "sheet.png",
    )
    sheet_spec = json.loads(json.dumps(source_character["poseArtworkSpecs"]["poseExpansionSheet"]))
    equal_quadrants = {
        "reading": [0, 0, 615, 639],
        "presenting": [615, 0, 615, 639],
        "box-support": [0, 639, 615, 639],
        "resting": [615, 639, 615, 639],
    }
    for sprite_id, source_rect in equal_quadrants.items():
        sheet_spec["sprites"][sprite_id]["sourceRect"] = source_rect
    manifest = {
        "id": "sheet-edge-fixture",
        "version": "1",
        "title": "sheet edge fixture",
        "renderers": ["scene2d"],
        "defaultRenderer": "scene2d",
        "character": {
            "poseAssetGateVersion": 3,
            "poses": list(equal_quadrants),
            "poseBundles": {
                pose_id: source_character["poseBundles"][pose_id]
                for pose_id in equal_quadrants
            },
            "poseArtworkSpecs": {"poseExpansionSheet": sheet_spec},
        },
        "assets": {"poseExpansionSheet": "assets/sheet.png"},
    }
    manifest_path = theme_root / "theme.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_pose_assets(
        manifest_path,
        PROJECT_ROOT / "qml" / "V03PetBody.qml",
    )

    assert report["passed"] is False
    sprite_report = report["assets"]["poseExpansionSheet"]["spriteSheet"]
    assert sprite_report["passed"] is False
    assert all(
        "touches its clip edge" in "\n".join(value["errors"])
        for value in sprite_report["sprites"].values()
    )
