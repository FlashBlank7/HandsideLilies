from __future__ import annotations

import json
import hashlib
import math
import os
import sys
from pathlib import Path

# This is a real QML runtime probe, but it must never display on the desktop.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QObject, QPointF, Property, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class HarnessBackend(QObject):
    habitatChanged = Signal()
    wardrobeChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        manifest_path = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        theme_root = manifest_path.parent
        self._asset_urls: dict[str, QUrl] = {}
        for key, value in dict(self._manifest.get("assets", {})).items():
            path = (theme_root / str(value)).resolve()
            if path.is_file():
                self._asset_urls[str(key)] = QUrl.fromLocalFile(str(path))
        self._habitat: dict[str, object] = {}
        self._wardrobe = {"current": {"outfit_id": "first-encounter"}}

    @Property("QVariantMap", notify=habitatChanged)
    def habitatState(self) -> dict[str, object]:
        return dict(self._habitat)

    @Property("QVariantMap", constant=True)
    def themeManifest(self) -> dict[str, object]:
        return dict(self._manifest)

    @Property("QVariantMap", notify=wardrobeChanged)
    def wardrobeState(self) -> dict[str, object]:
        return dict(self._wardrobe)

    @Slot(str, result=QUrl)
    def assetUrl(self, key: str) -> QUrl:
        return self._asset_urls.get(str(key), QUrl())

    def set_habitat(self, value: dict[str, object]) -> None:
        self._habitat = dict(value)
        self.habitatChanged.emit()

    def set_outfit(self, outfit_id: str) -> None:
        self._wardrobe = {"current": {"outfit_id": str(outfit_id)}}
        self.wardrobeChanged.emit()


HARNESS = b"""
import QtQuick
import QtQuick.Window

Window {
    width: 700
    height: 660
    visible: true
    color: "transparent"

    V03PetBody {
        objectName: "proceduralHabitatPet"
        anchors.fill: parent
        appBackend: backend
        characterHeight: 400
        pose: {
            var value = String(backend.habitatState.pose || "")
            if (value.indexOf("edge-peek") === 0)
                return "edge-peek-live"
            if (value === "title-sit")
                return "title-sit"
            if (value === "perch-top")
                return "perch-prone"
            return "idle-prayer"
        }
    }
}
"""


def _state(
    *,
    profile: str,
    variant: str,
    pose: str,
    contact_x: float,
    contact_y: float,
    motion: str,
    strategy: str,
    mirror: bool = False,
) -> dict[str, object]:
    return {
        "attached": True,
        "profile": profile,
        "poseVariant": variant,
        "pose": pose,
        "habitatStrategy": strategy,
        "characterScale": 0.82,
        "anchorNormX": 0.50,
        "anchorNormY": 0.48,
        "contactX": contact_x,
        "contactY": contact_y,
        "mirror": mirror,
        "motionStyle": motion,
        "motionPeriod": 4.0,
        "peekFraction": 1.0,
    }


def main() -> int:
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([])
    backend = HarnessBackend()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.loadData(
        HARNESS,
        QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "ProceduralHabitatHarness.qml")),
    )
    if not engine.rootObjects():
        raise RuntimeError("procedural habitat harness failed to load")
    window = engine.rootObjects()[0]
    pet = window.findChild(QQuickItem, "proceduralHabitatPet")
    frame = window.findChild(QQuickItem, "petFigureFrame")
    hit_area = window.findChild(QQuickItem, "desktopPetCharacterHitMask")
    if pet is None or frame is None or hit_area is None:
        raise RuntimeError("procedural habitat layers failed to load")
    # Prove the regional construction is not merely three labels wrapped
    # around three whole-frame angles.
    pet.setProperty("layeredBodyRotationMultiplier", 0.0)
    pet.setProperty("paused", True)

    variants = {
        "micro-corner-grip": _state(
            profile="micro-window-edge",
            variant="micro-corner-grip",
            pose="edge-peek-left",
            contact_x=0.28,
            contact_y=0.64,
            motion="corner-grip",
            strategy="tiny",
        ),
        "compact-title-curl": _state(
            profile="small-title",
            variant="compact-title-curl",
            pose="title-sit",
            contact_x=0.60,
            contact_y=0.72,
            motion="title-curl",
            strategy="small",
        ),
        "window-perch-tucked": _state(
            profile="medium-perch",
            variant="window-perch-tucked",
            pose="perch-top",
            contact_x=0.50,
            contact_y=0.94,
            motion="perch-tuck",
            strategy="medium",
        ),
        "window-perch": _state(
            profile="medium-perch",
            variant="window-perch",
            pose="perch-top",
            contact_x=0.50,
            contact_y=0.94,
            motion="perch-breathe",
            strategy="medium",
        ),
        "wide-window-sprawl": _state(
            profile="large-perch",
            variant="wide-window-sprawl",
            pose="perch-top",
            contact_x=0.50,
            contact_y=0.94,
            motion="perch-stretch",
            strategy="large",
        ),
        "panoramic-prone": _state(
            profile="large-perch",
            variant="panoramic-prone",
            pose="perch-top",
            contact_x=0.50,
            contact_y=0.94,
            motion="perch-drift",
            strategy="large",
        ),
        "screen-edge-watch": _state(
            profile="maximized-edge",
            variant="screen-edge-watch",
            pose="edge-peek-right",
            contact_x=0.18,
            contact_y=0.58,
            motion="screen-watch",
            strategy="maximized",
            mirror=True,
        ),
        "cautious-return": _state(
            profile="offscreen-window-edge",
            variant="cautious-return",
            pose="edge-peek-left",
            contact_x=0.34,
            contact_y=0.54,
            motion="cautious-peek",
            strategy="edge",
        ),
        "caption-side-lean": _state(
            profile="narrow-caption-edge",
            variant="caption-side-lean",
            pose="edge-peek-left",
            contact_x=0.12,
            contact_y=0.74,
            motion="caption-lean",
            strategy="edge",
        ),
    }
    outfits = (
        "first-encounter",
        "summer-cotton-dress",
        "home-cardigan",
        "reading-smock",
        "focus-coat",
        "rest-nightdress",
    )
    results: dict[str, object] = {}
    geometry_signatures: set[tuple[float, ...]] = set()
    regional_signatures: set[tuple[float, ...]] = set()
    pixel_signatures: set[str] = set()
    for variant_id, state in variants.items():
        backend.set_habitat(state)
        QTest.qWait(340)
        outfit_results: dict[str, object] = {}
        signature = (
            round(float(pet.property("renderedLayeredBodyRotation")), 3),
            round(float(pet.property("renderedVariantHeadRotation")), 3),
            round(float(pet.property("renderedVariantTorsoRotation")), 3),
            round(float(pet.property("renderedVariantSkirtRotation")), 3),
            round(float(pet.property("renderedVariantHeadOffsetX")), 4),
            round(float(pet.property("renderedVariantHeadOffsetY")), 4),
            round(float(pet.property("renderedVariantTorsoOffsetX")), 4),
            round(float(pet.property("renderedVariantTorsoOffsetY")), 4),
            round(float(pet.property("renderedVariantSkirtOffsetX")), 4),
            round(float(pet.property("renderedVariantSkirtOffsetY")), 4),
            round(float(pet.property("renderedVariantHeadScaleX")), 3),
            round(float(pet.property("renderedVariantHeadScaleY")), 3),
            round(float(pet.property("renderedVariantTorsoScaleX")), 3),
            round(float(pet.property("renderedVariantTorsoScaleY")), 3),
            round(float(pet.property("renderedVariantSkirtScaleX")), 3),
            round(float(pet.property("renderedVariantSkirtScaleY")), 3),
            round(float(pet.property("renderedVariantHeadClipEnd")), 3),
            round(float(pet.property("renderedVariantTorsoClipEnd")), 3),
        )
        geometry_signatures.add(signature)
        regional_signatures.add(signature[1:])
        variant_pixel_signature = ""
        variant_alpha_bbox: list[int] = []
        for outfit_id in outfits:
            backend.set_outfit(outfit_id)
            QTest.qWait(20)
            contact = frame.mapToItem(
                pet,
                QPointF(
                    float(frame.width()) * float(pet.property("renderedContactX")),
                    float(frame.height()) * float(pet.property("renderedContactY")),
                ),
            )
            anchor = (
                float(pet.width()) * float(pet.property("renderedAnchorNormX")),
                float(pet.height()) * float(pet.property("renderedAnchorNormY")),
            )
            anchor_error = math.hypot(
                float(contact.x()) - anchor[0], float(contact.y()) - anchor[1]
            )
            rendered_contact_y = float(pet.property("renderedContactY"))
            head_clip_end = float(pet.property("renderedVariantHeadClipEnd"))
            torso_clip_end = float(pet.property("renderedVariantTorsoClipEnd"))
            figure_bounds = (
                float(pet.property("figureLeft")),
                float(pet.property("figureTop")),
                float(pet.property("figureWidth")),
                float(pet.property("figureHeight")),
            )
            bounds_inside = (
                figure_bounds[0] >= -0.1
                and figure_bounds[1] >= -0.1
                and figure_bounds[2] > 24.0
                and figure_bounds[3] > 24.0
                and figure_bounds[0] + figure_bounds[2] <= float(pet.width()) + 0.1
                and figure_bounds[1] + figure_bounds[3] <= float(pet.height()) + 0.1
            )
            below_contact_extent = (
                figure_bounds[1] + figure_bounds[3] - anchor[1]
            )
            contact_in_shoulder_region = (
                head_clip_end <= rendered_contact_y <= torso_clip_end
            ) if variant_id.startswith("window-perch") else True
            local_hit = QPointF(float(frame.width()) * 0.50, float(frame.height()) * 0.46)
            root_hit = frame.mapToItem(pet, local_hit)
            hit_valid = bool(
                pet.containsCharacterPoint(float(root_hit.x()), float(root_hit.y()))
            )
            if outfit_id == "first-encounter":
                rendered = window.grabWindow().convertToFormat(QImage.Format_RGBA8888)
                width, height = rendered.width(), rendered.height()
                payload = bytes(rendered.bits())
                alpha = payload[3::4]
                solid_points = [
                    (index % width, index // width)
                    for index, value in enumerate(alpha)
                    if value >= 16
                ]
                if solid_points:
                    xs = [point[0] for point in solid_points]
                    ys = [point[1] for point in solid_points]
                    variant_alpha_bbox = [
                        min(xs), min(ys), max(xs) + 1, max(ys) + 1
                    ]
                variant_pixel_signature = hashlib.sha256(alpha).hexdigest()
                pixel_signatures.add(variant_pixel_signature)
            outfit_results[outfit_id] = {
                "usesPoseArtwork": bool(pet.property("usesPoseArtwork")),
                "poseArtworkKey": str(pet.property("poseArtworkKey")),
                "outfitAssetKey": str(pet.property("outfitAssetKey")),
                "anchorError": round(anchor_error, 5),
                "renderedContactY": round(rendered_contact_y, 4),
                "backendContactY": float(state["contactY"]),
                "contactInShoulderRegion": contact_in_shoulder_region,
                "belowContactExtent": round(below_contact_extent, 3),
                "figureBounds": [round(value, 3) for value in figure_bounds],
                "boundsInside": bounds_inside,
                "hitAreaEnabled": bool(hit_area.isEnabled()),
                "hitValid": hit_valid,
                "passed": (
                    not bool(pet.property("usesPoseArtwork"))
                    and str(pet.property("poseArtworkKey")) == ""
                    and str(pet.property("outfitAssetKey")) != ""
                    and anchor_error <= 0.05
                    and (
                        not variant_id.startswith("window-perch")
                        or (
                            rendered_contact_y <= 0.50
                            and rendered_contact_y
                            != float(state["contactY"])
                            and contact_in_shoulder_region
                            and below_contact_extent >= 60.0
                        )
                    )
                    and bounds_inside
                    and bool(hit_area.isEnabled())
                    and hit_valid
                ),
            }
        results[variant_id] = {
            "geometrySignature": list(signature),
            "regionalSignatureWithoutBodyRotation": list(signature[1:]),
            "pixelAlphaSha256WithoutBodyRotation": variant_pixel_signature,
            "pixelAlphaBoundsWithoutBodyRotation": variant_alpha_bbox,
            "outfits": outfit_results,
            "passed": all(bool(value["passed"]) for value in outfit_results.values()),
        }

    optional_urls_empty = {
        key: backend.assetUrl(key).isEmpty()
        for key in (
            "poseEdgeLeanV1",
            "poseMicroCornerGripV1",
            "poseWideWindowSprawlV1",
            "poseWindowDangleV1",
            "poseWindowProneV2",
        )
    }
    passed = (
        len(geometry_signatures) == len(variants)
        and len(regional_signatures) == len(variants)
        and len(pixel_signatures) == len(variants)
        and all(bool(value["passed"]) for value in results.values())
        and all(optional_urls_empty.values())
    )
    report = {
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "variantCount": len(variants),
        "outfitCount": len(outfits),
        "uniqueGeometrySignatureCount": len(geometry_signatures),
        "uniqueRegionalSignatureCountWithoutBodyRotation": len(regional_signatures),
        "uniquePixelSignatureCountWithoutBodyRotation": len(pixel_signatures),
        "optionalAssetUrlsEmpty": optional_urls_empty,
        "variants": results,
        "passed": passed,
    }
    report_path = PROJECT_ROOT / "artifacts" / "procedural-habitat-pose-gate.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report["report"] = str(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    app.quit()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
