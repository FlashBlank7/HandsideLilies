from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# This verifier must never map a native window onto the user's desktop.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


POSES = ("focus-watch", "reading", "presenting", "box-support", "resting")


class PolicyBackend(QObject):
    wardrobeChanged = Signal()
    habitatChanged = Signal()

    def __init__(self, manifest_path: Path) -> None:
        super().__init__()
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._outfit = "first-encounter"
        self._asset_urls = {
            str(key): QUrl.fromLocalFile(str((manifest_path.parent / str(value)).resolve()))
            for key, value in dict(self._manifest.get("assets", {})).items()
        }

    @Property("QVariantMap", constant=True)
    def themeManifest(self) -> dict[str, object]:
        return dict(self._manifest)

    @Property("QVariantMap", notify=wardrobeChanged)
    def wardrobeState(self) -> dict[str, object]:
        return {"current": {"outfit_id": self._outfit}}

    @Property("QVariantMap", notify=habitatChanged)
    def habitatState(self) -> dict[str, object]:
        return {}

    @Slot(str, result=QUrl)
    def assetUrl(self, key: str) -> QUrl:
        return self._asset_urls.get(str(key), QUrl())

    def set_outfit(self, outfit_id: str) -> None:
        self._outfit = str(outfit_id)
        self.wardrobeChanged.emit()


HARNESS = b"""
import QtQuick
import QtQuick.Window

Window {
    width: 385
    height: 363
    visible: true
    color: "transparent"

    V03PetBody {
        objectName: "policyPet"
        anchors.fill: parent
        appBackend: backend
        characterHeight: height * 2.0 / 3.0
        pose: "reading"
        paused: true
    }
}
"""


def _source_path(value: object) -> Path:
    return Path(value.toLocalFile()).resolve() if isinstance(value, QUrl) else Path()


def main() -> int:
    manifest_path = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    character = dict(manifest["character"])
    pose_bundles = dict(character["poseBundles"])
    outfit_bundles = dict(character["outfitBundles"])
    declared_outfits = tuple(str(value) for value in character["outfits"])

    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([])
    backend = PolicyBackend(manifest_path)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.loadData(
        HARNESS,
        QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "PoseOutfitPolicyHarness.qml")),
    )
    if not engine.rootObjects():
        raise RuntimeError("pose/outfit policy harness failed to load")
    window = engine.rootObjects()[0]
    pet = window.findChild(QQuickItem, "policyPet")
    pose_frame = window.findChild(QQuickItem, "petPoseArtworkFrame")
    figure_frame = window.findChild(QQuickItem, "petFigureFrame")
    if pet is None or pose_frame is None or figure_frame is None:
        raise RuntimeError("pose/outfit policy layers failed to load")

    cases: list[dict[str, object]] = []
    for pose_id in POSES:
        definition = dict(pose_bundles[pose_id])
        compatible = tuple(str(value) for value in definition["compatibleOutfits"])
        if compatible == ("*",):
            compatible = declared_outfits
        artwork_outfits = tuple(str(value) for value in definition["artworkOutfits"])
        if artwork_outfits == ("*",):
            artwork_outfits = declared_outfits
        for outfit_id in compatible:
            pet.setProperty("pose", pose_id)
            backend.set_outfit(outfit_id)
            expected_artwork = outfit_id in artwork_outfits
            # A cold sprite sheet can legitimately take longer than the
            # 220 ms visual fade to decode.  The runtime contract is readiness,
            # not a fixed wall-clock guess: keep sampling until the requested
            # renderer has fully committed (or fail after a bounded timeout).
            for _ready_sample in range(150):
                app.processEvents()
                artwork_blend = float(pet.property("renderedArtworkBlend"))
                renderer_ready = (
                    bool(pose_frame.isVisible())
                    and not bool(figure_frame.isVisible())
                    and artwork_blend >= 0.999
                    if expected_artwork
                    else (
                        not bool(pose_frame.isVisible())
                        and bool(figure_frame.isVisible())
                        and artwork_blend <= 0.001
                    )
                )
                if renderer_ready:
                    break
                QTest.qWait(10)

            outfit_asset = str(dict(outfit_bundles[outfit_id])["asset"])
            expected_outfit_path = Path(
                backend.assetUrl(outfit_asset).toLocalFile()
            ).resolve()
            actual_outfit_path = _source_path(pet.property("outfitSource"))
            uses_artwork = bool(pet.property("usesPoseArtwork"))
            artwork_blend = float(pet.property("renderedArtworkBlend"))
            layers_match = (
                pose_frame.isVisible()
                and not figure_frame.isVisible()
                and artwork_blend >= 0.999
                if expected_artwork
                else (
                    not pose_frame.isVisible()
                    and figure_frame.isVisible()
                    and artwork_blend <= 0.001
                )
            )
            passed = (
                uses_artwork is expected_artwork
                and actual_outfit_path == expected_outfit_path
                and str(pet.property("outfitAssetKey")) == outfit_asset
                and layers_match
            )
            cases.append(
                {
                    "pose": pose_id,
                    "outfit": outfit_id,
                    "expectedRenderer": "baked-pose" if expected_artwork else "layered-outfit",
                    "usesPoseArtwork": uses_artwork,
                    "artworkBlend": round(artwork_blend, 4),
                    "outfitAsset": outfit_asset,
                    "layersMatch": layers_match,
                    "passed": passed,
                }
            )

    report = {
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "cases": cases,
        "bakedCount": sum(value["expectedRenderer"] == "baked-pose" for value in cases),
        "layeredFallbackCount": sum(
            value["expectedRenderer"] == "layered-outfit" for value in cases
        ),
        "passed": bool(cases) and all(bool(value["passed"]) for value in cases),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))

    window.setPersistentGraphics(False)
    window.setPersistentSceneGraph(False)
    window.hide()
    window.close()
    window.releaseResources()
    window.deleteLater()
    engine.clearComponentCache()
    engine.collectGarbage()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
