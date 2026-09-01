from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VALID_RENDERERS = {"scene2d", "video"}


def _normalized_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _validate_normalized_rect(value: Any, *, label: str) -> None:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must contain four normalized numbers")
    numbers = [_normalized_number(item) for item in value]
    if any(item is None for item in numbers):
        raise ValueError(f"{label} must contain four normalized numbers")
    left, top, width, height = (float(item) for item in numbers)
    if width <= 0.0 or height <= 0.0 or left + width > 1.000001 or top + height > 1.000001:
        raise ValueError(f"{label} must stay inside normalized artwork bounds")


def _validate_normalized_ellipse(value: Any, *, label: str) -> None:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must contain center-x, center-y, radius-x, radius-y")
    numbers = [_normalized_number(item) for item in value]
    if any(item is None for item in numbers):
        raise ValueError(f"{label} must contain normalized numbers")
    center_x, center_y, radius_x, radius_y = (float(item) for item in numbers)
    if (
        radius_x <= 0.0
        or radius_y <= 0.0
        or center_x - radius_x < -0.000001
        or center_x + radius_x > 1.000001
        or center_y - radius_y < -0.000001
        or center_y + radius_y > 1.000001
    ):
        raise ValueError(f"{label} must stay inside normalized artwork bounds")


def _validate_normalized_polygon(value: Any, *, label: str) -> None:
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError(f"{label} must contain at least three normalized points")
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{label}[{index}] must be a normalized point")
        if any(_normalized_number(item) is None for item in point):
            raise ValueError(f"{label}[{index}] must be a normalized point")


def validate_pose_click_mask(value: Any, *, label: str) -> None:
    """Validate the small declarative hit-mask language consumed by QML.

    A mask may be one normalized rect/ellipse or a composite union of rects,
    ellipses and polygons.  Keeping this data in the theme prevents a future
    pose from inheriting the oversized fallback silhouette of an unrelated
    bitmap.
    """

    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    mask_type = str(value.get("type", ""))
    shape_count = 0
    if mask_type == "rect":
        _validate_normalized_rect(value.get("rect"), label=f"{label}.rect")
        shape_count += 1
    elif mask_type == "ellipse":
        _validate_normalized_ellipse(value.get("ellipse"), label=f"{label}.ellipse")
        shape_count += 1
    elif mask_type != "composite":
        raise ValueError(f"{label}.type must be rect, ellipse, or composite")

    for collection_name, validator in (
        ("rects", _validate_normalized_rect),
        ("ellipses", _validate_normalized_ellipse),
        ("polygons", _validate_normalized_polygon),
    ):
        collection = value.get(collection_name, [])
        if not isinstance(collection, list):
            raise ValueError(f"{label}.{collection_name} must be an array")
        for index, shape in enumerate(collection):
            validator(shape, label=f"{label}.{collection_name}[{index}]")
            shape_count += 1
    if shape_count == 0:
        raise ValueError(f"{label} must declare at least one shape")


@dataclass(frozen=True)
class PoseBundle:
    pose_id: str
    recipe: str
    click_mask: str
    compatible_outfits: tuple[str, ...]
    artwork_asset: str = ""
    sprite_id: str = ""
    artwork_outfits: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        result = {
            "id": self.pose_id,
            "recipe": self.recipe,
            "clickMask": self.click_mask,
            "compatibleOutfits": list(self.compatible_outfits),
        }
        if self.artwork_asset:
            result["artworkAsset"] = self.artwork_asset
        if self.sprite_id:
            result["spriteId"] = self.sprite_id
        if self.artwork_outfits:
            result["artworkOutfits"] = list(self.artwork_outfits)
        return result


@dataclass(frozen=True)
class OutfitBundle:
    outfit_id: str
    asset_key: str
    anchor_version: int

    def public(self) -> dict[str, Any]:
        return {"id": self.outfit_id, "asset": self.asset_key, "anchorVersion": self.anchor_version}


@dataclass(frozen=True)
class ThemeManifest:
    theme_id: str
    version: str
    title: str
    renderers: tuple[str, ...]
    default_renderer: str
    intro: dict[str, Any]
    palette: dict[str, str]
    dock: dict[str, Any]
    ui: dict[str, Any]
    character: dict[str, Any]
    icon_pack: str
    assets: dict[str, str]
    performance: dict[str, Any]
    root: Path

    @classmethod
    def load(cls, path: Path) -> "ThemeManifest":
        raw = json.loads(path.read_text("utf-8"))
        renderers = tuple(str(value) for value in raw.get("renderers", []))
        if not renderers or any(value not in VALID_RENDERERS for value in renderers):
            raise ValueError("theme renderers must contain only scene2d/video")
        default_renderer = str(raw.get("defaultRenderer", renderers[0]))
        if default_renderer not in renderers:
            raise ValueError("defaultRenderer is not available")
        assets = {str(k): str(v) for k, v in raw.get("assets", {}).items()}
        for relative in assets.values():
            if relative and not (path.parent / relative).resolve().is_relative_to(path.parent.resolve()):
                raise ValueError("theme asset escapes theme root")
        icon_pack = str(raw.get("iconPack", ""))
        if icon_pack and not (path.parent / icon_pack).resolve().is_relative_to(path.parent.resolve()):
            raise ValueError("theme icon pack escapes theme root")
        if icon_pack and not (path.parent / icon_pack).is_file():
            raise ValueError("theme icon pack does not exist")
        character = dict(raw.get("character", {}))
        poses = {str(value) for value in character.get("poses", [])}
        outfits = {str(value) for value in character.get("outfits", [])}
        default_pose = str(character.get("defaultPose", ""))
        default_outfit = str(character.get("defaultOutfit", ""))
        if default_pose and default_pose not in poses:
            raise ValueError("character.defaultPose is not declared")
        if default_outfit and default_outfit not in outfits:
            raise ValueError("character.defaultOutfit is not declared")
        pose_specs = dict(character.get("poseArtworkSpecs", {}))
        for asset_key, specification in pose_specs.items():
            if not isinstance(specification, dict):
                raise ValueError(f"pose artwork spec must be an object: {asset_key}")
            sprites = specification.get("sprites")
            if isinstance(sprites, dict) and sprites:
                for sprite_id, sprite in sprites.items():
                    if not isinstance(sprite, dict):
                        raise ValueError(
                            f"pose artwork sprite must be an object: {asset_key}:{sprite_id}"
                        )
                    validate_pose_click_mask(
                        sprite.get("clickMask"),
                        label=f"pose artwork sprite clickMask: {asset_key}:{sprite_id}",
                    )
            else:
                validate_pose_click_mask(
                    specification.get("clickMask"),
                    label=f"pose artwork clickMask: {asset_key}",
                )
        for pose_id, definition in dict(character.get("poseBundles", {})).items():
            if str(pose_id) not in poses or not isinstance(definition, dict):
                raise ValueError("pose bundle is not declared by character.poses")
            compatible_outfits = tuple(
                str(value) for value in definition.get("compatibleOutfits", ["*"])
            )
            if not compatible_outfits:
                raise ValueError(f"pose bundle has no compatible outfits: {pose_id}")
            if "*" in compatible_outfits and compatible_outfits != ("*",):
                raise ValueError(
                    f"pose bundle wildcard must be the only compatible outfit: {pose_id}"
                )
            unknown_compatible = {
                value for value in compatible_outfits
                if value != "*" and value not in outfits
            }
            if unknown_compatible:
                raise ValueError(
                    f"pose bundle declares unknown compatible outfits: {pose_id}"
                )
            if str(definition.get("recipe", "")) != "pose-artwork":
                if definition.get("artworkOutfits"):
                    raise ValueError(
                        f"non-artwork pose bundle cannot declare artworkOutfits: {pose_id}"
                    )
                continue
            asset_key = str(definition.get("artworkAsset", ""))
            if not asset_key or asset_key not in assets:
                raise ValueError(f"pose bundle asset is missing: {asset_key}")
            artwork_outfits = tuple(
                str(value) for value in definition.get("artworkOutfits", [])
            )
            if not artwork_outfits:
                raise ValueError(
                    f"pose artwork bundle must declare artworkOutfits: {pose_id}"
                )
            if "*" in artwork_outfits and artwork_outfits != ("*",):
                raise ValueError(
                    f"pose artwork wildcard must be the only artwork outfit: {pose_id}"
                )
            resolved_compatible = outfits if compatible_outfits == ("*",) else set(compatible_outfits)
            resolved_artwork = outfits if artwork_outfits == ("*",) else set(artwork_outfits)
            if not resolved_artwork <= resolved_compatible:
                raise ValueError(
                    f"pose artwork outfits must also be compatible: {pose_id}"
                )
            if any(value != "*" and value not in outfits for value in artwork_outfits):
                raise ValueError(
                    f"pose bundle declares unknown artwork outfits: {pose_id}"
                )
            specification = pose_specs.get(asset_key)
            if not isinstance(specification, dict):
                raise ValueError(f"pose bundle artwork spec is missing: {asset_key}")
            sprites = specification.get("sprites")
            sprite_id = str(definition.get("spriteId", ""))
            if isinstance(sprites, dict) and sprites:
                sprite = sprites.get(sprite_id)
                if not sprite_id or not isinstance(sprite, dict):
                    raise ValueError(
                        f"pose bundle sprite is missing: {pose_id} -> {asset_key}:{sprite_id}"
                    )
                if str(sprite.get("poseId", "")) != str(pose_id):
                    raise ValueError(
                        f"pose bundle sprite poseId does not match: {pose_id}"
                    )
            elif sprite_id:
                raise ValueError(f"non-sheet pose bundle cannot declare spriteId: {pose_id}")
        habitat_variants = character.get("habitatPoseVariants", {})
        if habitat_variants and not isinstance(habitat_variants, dict):
            raise ValueError("character.habitatPoseVariants must be an object")
        for variant_id, definition in dict(habitat_variants or {}).items():
            if not isinstance(definition, dict):
                raise ValueError(f"habitat pose variant must be an object: {variant_id}")
            asset_key = str(definition.get("optionalArtworkAsset", ""))
            enabled = bool(definition.get("optionalArtworkEnabled", False))
            if not asset_key:
                continue
            if asset_key not in assets:
                raise ValueError(
                    f"optional habitat artwork asset is missing: {variant_id} -> {asset_key}"
                )
            specification = pose_specs.get(asset_key)
            if not isinstance(specification, dict) or not bool(specification.get("optional")):
                raise ValueError(
                    f"optional habitat artwork spec is missing: {variant_id} -> {asset_key}"
                )
            artwork_outfits = tuple(
                str(value) for value in definition.get("artworkOutfits", [])
            )
            if not artwork_outfits:
                raise ValueError(
                    f"optional habitat artwork must declare artworkOutfits: {variant_id}"
                )
            if "*" in artwork_outfits and artwork_outfits != ("*",):
                raise ValueError(
                    f"optional habitat artwork wildcard must be the only outfit: {variant_id}"
                )
            if any(value != "*" and value not in outfits for value in artwork_outfits):
                raise ValueError(
                    f"optional habitat artwork declares unknown outfits: {variant_id}"
                )
            if enabled:
                asset_path = (path.parent / assets[asset_key]).resolve()
                if not asset_path.is_file():
                    raise ValueError(
                        f"enabled optional habitat artwork file is missing: "
                        f"{variant_id} -> {asset_key}"
                    )
        for outfit_id, definition in dict(character.get("outfitBundles", {})).items():
            if str(outfit_id) not in outfits or not isinstance(definition, dict):
                raise ValueError("outfit bundle is not declared by character.outfits")
            asset_key = str(definition.get("asset", ""))
            if asset_key not in assets:
                raise ValueError(f"outfit bundle asset is missing: {asset_key}")
        return cls(
            theme_id=str(raw["id"]),
            version=str(raw["version"]),
            title=str(raw["title"]),
            renderers=renderers,
            default_renderer=default_renderer,
            intro=dict(raw.get("intro", {})),
            palette=dict(raw.get("palette", {})),
            dock=dict(raw.get("dock", {})),
            ui=dict(raw.get("ui", {})),
            character=character,
            icon_pack=icon_pack,
            assets=assets,
            performance=dict(raw.get("performance", {})),
            root=path.parent,
        )

    def asset(self, key: str) -> Path | None:
        relative = self.assets.get(key)
        if not relative:
            return None
        path = (self.root / relative).resolve()
        return path if path.exists() else None

    def pose_bundles(self) -> tuple[PoseBundle, ...]:
        bundles = dict(self.character.get("poseBundles", {}))
        return tuple(
            PoseBundle(
                pose_id=str(pose_id),
                recipe=str(value.get("recipe", pose_id)),
                click_mask=str(value.get("clickMask", "character")),
                compatible_outfits=tuple(str(item) for item in value.get("compatibleOutfits", ["*"])),
                artwork_asset=str(value.get("artworkAsset", "")),
                sprite_id=str(value.get("spriteId", "")),
                artwork_outfits=tuple(
                    str(item) for item in value.get("artworkOutfits", [])
                ),
            )
            for pose_id, value in bundles.items()
            if isinstance(value, dict)
        )

    def compatible_pose_ids(self, outfit_id: str) -> tuple[str, ...]:
        """Return the manifest-authoritative pose order for an outfit.

        Compatibility is declared pose-first in ``poseBundles`` because the
        renderer owns pose recipes.  Keeping the projection here prevents the
        wardrobe service and QML from growing separate, drifting rule tables.
        """

        outfit = str(outfit_id)
        declared_outfits = {
            str(value) for value in self.character.get("outfits", [])
        }
        if outfit not in declared_outfits:
            return ()
        return tuple(
            bundle.pose_id
            for bundle in self.pose_bundles()
            if "*" in bundle.compatible_outfits
            or outfit in bundle.compatible_outfits
        )

    def pose_accepts_outfit(self, pose_id: str, outfit_id: str) -> bool:
        return str(pose_id) in self.compatible_pose_ids(str(outfit_id))

    def outfit_bundles(self) -> tuple[OutfitBundle, ...]:
        bundles = dict(self.character.get("outfitBundles", {}))
        return tuple(
            OutfitBundle(
                outfit_id=str(outfit_id),
                asset_key=str(value.get("asset", "desktopPet")),
                anchor_version=max(1, int(value.get("anchorVersion", 1))),
            )
            for outfit_id, value in bundles.items()
            if isinstance(value, dict)
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.theme_id,
            "version": self.version,
            "title": self.title,
            "renderers": list(self.renderers),
            "defaultRenderer": self.default_renderer,
            "intro": self.intro,
            "palette": self.palette,
            "dock": self.dock,
            "ui": self.ui,
            "character": self.character,
            "iconPack": self.icon_pack,
            "assets": dict(self.assets),
            "performance": self.performance,
        }
