from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from verify_outfit_assets import validate_outfit_assets


class OutfitHarnessBackend(QObject):
    habitatChanged = Signal()
    wardrobeChanged = Signal()

    def __init__(self, manifest: dict[str, object], theme_root: Path) -> None:
        super().__init__()
        self._manifest = dict(manifest)
        self._asset_urls = {
            str(key): QUrl.fromLocalFile(str((theme_root / str(value)).resolve()))
            for key, value in dict(manifest.get("assets", {})).items()
        }
        self._wardrobe_state: dict[str, object] = {
            "current": {"outfit_id": "first-encounter"}
        }

    @Property("QVariantMap", notify=habitatChanged)
    def habitatState(self) -> dict[str, object]:
        return {}

    @Property("QVariantMap", constant=True)
    def themeManifest(self) -> dict[str, object]:
        return dict(self._manifest)

    @Property("QVariantMap", notify=wardrobeChanged)
    def wardrobeState(self) -> dict[str, object]:
        return dict(self._wardrobe_state)

    @Slot(str, result=QUrl)
    def assetUrl(self, key: str) -> QUrl:
        return self._asset_urls.get(str(key), QUrl())

    def set_outfit(self, outfit_id: str) -> None:
        self._wardrobe_state = {"current": {"outfit_id": str(outfit_id)}}
        self.wardrobeChanged.emit()


HARNESS = b"""
import QtQuick
import QtQuick.Window

Window {
    width: 520
    height: 700
    visible: true
    color: "transparent"

    V03PetBody {
        objectName: "outfitPet"
        anchors.fill: parent
        appBackend: backend
        characterHeight: 600
        pose: "idle-prayer"
        paused: true
    }
}
"""


def _source_path(item: QObject) -> str:
    value = item.property("source")
    if isinstance(value, QUrl):
        return str(Path(value.toLocalFile()).resolve()) if value.isLocalFile() else value.toString()
    text = str(value)
    url = QUrl(text)
    return str(Path(url.toLocalFile()).resolve()) if url.isLocalFile() else text


def _wait_for_sources(items: tuple[QObject, ...], expected: str) -> bool:
    for _index in range(50):
        if all(_source_path(item) == expected for item in items):
            return True
        QTest.qWait(10)
    return False


def main() -> int:
    manifest_path = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
    qml_path = PROJECT_ROOT / "qml" / "V03PetBody.qml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset_gate = validate_outfit_assets(manifest_path, qml_path)
    character = dict(manifest["character"])
    bundles = dict(character["outfitBundles"])
    specs = dict(character["outfitArtworkSpecs"])
    theme_root = manifest_path.parent

    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([])
    backend = OutfitHarnessBackend(manifest, theme_root)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.loadData(HARNESS, QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "OutfitHarness.qml")))
    if not engine.rootObjects():
        raise RuntimeError("outfit QML harness failed to load")
    window = engine.rootObjects()[0]
    pet = window.findChild(QQuickItem, "outfitPet")
    frame = window.findChild(QQuickItem, "petFigureFrame")
    head = window.findChild(QQuickItem, "petHairHeadImage")
    shoulder = window.findChild(QQuickItem, "petShoulderHandsImage")
    skirt = window.findChild(QQuickItem, "petSkirtImage")
    if any(value is None for value in (pet, frame, head, shoulder, skirt)):
        raise RuntimeError("outfit layers failed to load")
    assert pet is not None and frame is not None
    assert head is not None and shoulder is not None and skirt is not None
    image_items = (head, shoulder, skirt)

    results: dict[str, object] = {}
    reference_foot_y: float | None = None
    reference_center_x: float | None = None
    observed_sources: dict[str, list[str]] = {}
    for outfit_id in character["outfits"]:
        outfit_id = str(outfit_id)
        backend.set_outfit(outfit_id)
        QTest.qWait(25)
        bundle = dict(bundles[outfit_id])
        spec = dict(specs[outfit_id])
        asset_key = str(bundle["asset"])
        expected_source = str((theme_root / manifest["assets"][asset_key]).resolve())
        sources_ready = _wait_for_sources(image_items, expected_source)

        frame_width = float(frame.width())
        frame_height = float(frame.height())
        frame_ratio = frame_width / max(1.0, frame_height)
        expected_ratio = float(spec["aspectRatio"])
        solid_center = float(pet.property("outfitSolidCenterX"))
        feet_y = float(pet.property("outfitFeetY"))
        horizontal_offset = float(pet.property("outfitHorizontalOffset"))
        vertical_offset = float(pet.property("outfitVerticalOffset"))
        foot_world_y = float(frame.y()) + (feet_y + vertical_offset) * frame_height
        center_world_x = float(frame.x()) + (solid_center + horizontal_offset) * frame_width
        if reference_foot_y is None:
            reference_foot_y = foot_world_y
            reference_center_x = center_world_x

        expected_image_x = horizontal_offset * frame_width
        expected_image_y = vertical_offset * frame_height
        slice_offsets = {
            "head": [float(head.x()), float(head.y())],
            "shoulder": [
                float(shoulder.x()),
                float(shoulder.y()) + frame_height * 0.315,
            ],
            "skirt": [
                float(skirt.x()),
                float(skirt.y()) + frame_height * 0.600,
            ],
        }
        slice_offsets_consistent = all(
            abs(value[0] - expected_image_x) <= 0.05
            and abs(value[1] - expected_image_y) <= 0.05
            for value in slice_offsets.values()
        )
        source_values = [_source_path(item) for item in image_items]
        observed_sources.setdefault(expected_source, []).append(outfit_id)
        anchor_version = int(pet.property("outfitAnchorVersion"))
        passed = (
            str(pet.property("outfitId")) == outfit_id
            and str(pet.property("outfitAssetKey")) == asset_key
            and all(value == expected_source for value in source_values)
            and sources_ready
            and abs(frame_ratio - expected_ratio) <= 1e-6
            and anchor_version == int(bundle["anchorVersion"]) == int(spec["anchorVersion"])
            and slice_offsets_consistent
            and reference_foot_y is not None
            and abs(foot_world_y - reference_foot_y) <= 0.05
            and reference_center_x is not None
            and abs(center_world_x - reference_center_x) <= 0.05
        )
        results[outfit_id] = {
            "assetKey": asset_key,
            "source": expected_source,
            "sourceValues": source_values,
            "sourcesReady": sources_ready,
            "frameSize": [round(frame_width, 4), round(frame_height, 4)],
            "frameRatio": round(frame_ratio, 9),
            "expectedRatio": round(expected_ratio, 9),
            "anchorVersion": anchor_version,
            "solidCenterX": round(solid_center, 9),
            "feetY": round(feet_y, 9),
            "horizontalOffset": round(horizontal_offset, 9),
            "verticalOffset": round(vertical_offset, 9),
            "centerWorldX": round(center_world_x, 4),
            "footWorldY": round(foot_world_y, 4),
            "sliceOffsets": slice_offsets,
            "sliceOffsetsConsistent": slice_offsets_consistent,
            "implementationStatus": str(bundle.get("implementationStatus", "")),
            "visualAliasOf": str(bundle.get("visualAliasOf", "")),
            "passed": passed,
        }

    alias_sources_explicit = (
        results["summer-cotton-dress"]["source"] == results["first-encounter"]["source"]
        and results["summer-cotton-dress"]["implementationStatus"] == "visual-alias"
        and results["summer-cotton-dress"]["visualAliasOf"] == "first-encounter"
    )
    unique_source_count = len(observed_sources)
    passed = (
        bool(asset_gate["passed"])
        and all(bool(dict(value)["passed"]) for value in results.values())
        and alias_sources_explicit
        and unique_source_count == 5
    )
    report = {
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "assetGate": asset_gate,
        "outfits": results,
        "uniqueSourceCount": unique_source_count,
        "aliasSourcesExplicit": alias_sources_explicit,
        "passed": passed,
    }
    report_path = PROJECT_ROOT / "artifacts" / "outfit-runtime-gate.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report["report"] = str(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    app.quit()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
