from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lilies.core.database import Database
from lilies.core.productivity import OUTFIT_CATALOG, POSE_CATALOG, WardrobeService
from lilies.core.themes import ThemeManifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THEME_PATH = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"


def _unlock(database: Database, *item_keys: str) -> None:
    now = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        for item_key in item_keys:
            kind, _separator, _item_id = item_key.partition(":")
            connection.execute(
                """INSERT OR IGNORE INTO unlocks
                   (unlock_id,item_key,item_kind,reason,unlocked_at,metadata_json)
                   VALUES(?,?,?,?,?,'{}')""",
                (uuid.uuid4().hex, item_key, kind, "manifest-contract-test", now),
            )


def test_wardrobe_compatibility_is_projected_from_active_theme(tmp_path: Path) -> None:
    database = Database(tmp_path / "wardrobe.db")
    theme = ThemeManifest.load(THEME_PATH)
    wardrobe = WardrobeService(database, theme)
    state = wardrobe.list()
    canonical_pose_order = tuple(str(value["id"]) for value in POSE_CATALOG)

    for outfit in OUTFIT_CATALOG:
        outfit_id = str(outfit["id"])
        expected = tuple(
            pose_id
            for pose_id in canonical_pose_order
            if theme.pose_accepts_outfit(pose_id, outfit_id)
        )
        public = next(value for value in state["outfits"] if value["id"] == outfit_id)
        actual = canonical_pose_order if public["poses"] == "*" else tuple(public["poses"])
        assert actual == expected

    # These two assertions guard the exact drift that existed in the former
    # hard-coded outfit table: home-cardigan promised presenting, but the
    # renderer manifest allowed reading instead.
    home = next(value for value in state["outfits"] if value["id"] == "home-cardigan")
    assert "reading" in home["poses"]
    assert "presenting" not in home["poses"]

    _unlock(
        database,
        "outfit:home-cardigan",
        "pose:reading",
        "pose:presenting",
    )
    assert wardrobe.equip(outfit_id="home-cardigan", pose_id="reading")["pose_id"] == "reading"
    with pytest.raises(ValueError, match="not compatible"):
        wardrobe.equip(pose_id="presenting")


def test_every_baked_pose_declares_truthful_outfit_artwork_policy() -> None:
    theme = ThemeManifest.load(THEME_PATH)
    declared_outfits = set(theme.character["outfits"])
    fallback_count = 0
    for bundle in theme.pose_bundles():
        if not bundle.artwork_asset:
            assert bundle.artwork_outfits == ()
            continue
        assert bundle.artwork_outfits
        artwork_outfits = (
            declared_outfits
            if bundle.artwork_outfits == ("*",)
            else set(bundle.artwork_outfits)
        )
        compatible_outfits = (
            declared_outfits
            if bundle.compatible_outfits == ("*",)
            else set(bundle.compatible_outfits)
        )
        assert artwork_outfits <= compatible_outfits
        fallback_count += len(compatible_outfits - artwork_outfits)

    # Compatible non-baked combinations intentionally fall back to the
    # layered outfit renderer, so changing clothes remains visible.
    assert fallback_count >= 9


def test_visual_alias_dress_gets_the_same_baked_pose_coverage() -> None:
    """An exact artwork alias must not silently fall back to the standing pose.

    ``summer-cotton-dress`` currently points at the byte-identical approved
    first-encounter master.  Every baked first-encounter pose therefore also
    represents that alias truthfully; keeping it out of ``artworkOutfits``
    made window perching, edge peeking and the unlocked activity poses appear
    to do nothing after the user selected the summer dress.
    """

    raw = json.loads(THEME_PATH.read_text(encoding="utf-8"))
    assets = raw["assets"]
    assert assets["desktopPetSummer"] == assets["desktopPet"]

    theme = ThemeManifest.load(THEME_PATH)
    baked = [bundle for bundle in theme.pose_bundles() if bundle.artwork_asset]
    assert baked
    for bundle in baked:
        assert "first-encounter" in bundle.artwork_outfits
        assert "summer-cotton-dress" in bundle.artwork_outfits
        assert theme.pose_accepts_outfit(bundle.pose_id, "summer-cotton-dress")


def test_theme_rejects_pose_artwork_without_outfit_representation(tmp_path: Path) -> None:
    raw = json.loads(THEME_PATH.read_text(encoding="utf-8"))
    raw["iconPack"] = ""
    raw["character"]["poseBundles"]["reading"].pop("artworkOutfits")
    manifest = tmp_path / "theme.json"
    manifest.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="must declare artworkOutfits"):
        ThemeManifest.load(manifest)
