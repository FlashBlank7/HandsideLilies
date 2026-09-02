from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = PROJECT_ROOT / "artifacts" / "pose-click-mask-v0348.json"

from PySide6.QtCore import QObject, QPointF, Property, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class MaskBackend(QObject):
    poseChanged = Signal()
    habitatChanged = Signal()
    wardrobeChanged = Signal()

    def __init__(self, resource_root: Path) -> None:
        super().__init__()
        self.manifest_path = (
            resource_root / "themes" / "first-encounter" / "theme.json"
        )
        self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._asset_urls = {
            str(key): QUrl.fromLocalFile(str(path))
            for key, value in dict(self._manifest.get("assets", {})).items()
            if (path := (self.manifest_path.parent / str(value)).resolve()).is_file()
        }
        self._pose = "perch-prone"
        self._habitat: dict[str, object] = {}
        self._asset_overrides: dict[str, QUrl] = {}
        self._wardrobe = {"current": {"outfit_id": "first-encounter"}}

    @Property(str, notify=poseChanged)
    def pose(self) -> str:
        return self._pose

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
        if str(key) in self._asset_overrides:
            return self._asset_overrides[str(key)]
        return self._asset_urls.get(str(key), QUrl())

    def set_pose(self, pose: str) -> None:
        self._pose = str(pose)
        self.poseChanged.emit()

    def set_habitat(self, habitat: dict[str, object]) -> None:
        self._habitat = dict(habitat)
        self.habitatChanged.emit()

    def set_outfit(self, outfit_id: str) -> None:
        self._wardrobe = {"current": {"outfit_id": str(outfit_id)}}
        self.wardrobeChanged.emit()

    def override_asset(self, key: str, url: QUrl | None) -> None:
        if url is None:
            self._asset_overrides.pop(str(key), None)
        else:
            self._asset_overrides[str(key)] = url


HARNESS = b"""
import QtQuick
import QtQuick.Window

Window {
    width: 700
    height: 660
    visible: true
    color: "transparent"

    V03PetBody {
        objectName: "maskPet"
        anchors.fill: parent
        appBackend: backend
        characterHeight: 400
        pose: backend.pose
        paused: true
    }
}
"""


def _first_mask_point(mask: dict[str, object]) -> tuple[float, float]:
    if str(mask.get("type", "")) == "rect":
        left, top, width, height = (float(value) for value in mask["rect"])
        return left + width / 2.0, top + height / 2.0
    if str(mask.get("type", "")) == "ellipse":
        center_x, center_y, _radius_x, _radius_y = (
            float(value) for value in mask["ellipse"]
        )
        return center_x, center_y
    ellipses = list(mask.get("ellipses", []))
    if ellipses:
        center_x, center_y, _radius_x, _radius_y = (
            float(value) for value in ellipses[0]
        )
        return center_x, center_y
    rects = list(mask.get("rects", []))
    if rects:
        left, top, width, height = (float(value) for value in rects[0])
        return left + width / 2.0, top + height / 2.0
    points = list(list(mask.get("polygons", []))[0])
    return (
        sum(float(point[0]) for point in points) / len(points),
        sum(float(point[1]) for point in points) / len(points),
    )


def _hit(pet: QQuickItem, frame: QQuickItem, nx: float, ny: float) -> bool:
    point = frame.mapToItem(
        pet,
        QPointF(float(frame.width()) * nx, float(frame.height()) * ny),
    )
    return bool(pet.containsCharacterPoint(float(point.x()), float(point.y())))


def _interaction_hit(
    pet: QQuickItem,
    frame: QQuickItem,
    nx: float,
    ny: float,
) -> bool:
    point = frame.mapToItem(
        pet,
        QPointF(float(frame.width()) * nx, float(frame.height()) * ny),
    )
    return bool(
        pet.containsCharacterInteractionPoint(float(point.x()), float(point.y()))
    )


def _hit_grid(pet: QQuickItem, frame: QQuickItem) -> dict[tuple[int, int], bool]:
    return {
        (column, row): _hit(pet, frame, column / 20.0, row / 20.0)
        for row in range(1, 20)
        for column in range(1, 20)
    }


def _wait_for(
    app: QApplication,
    predicate: object,
    *,
    timeout_ms: int = 1500,
) -> bool:
    for _index in range(max(1, timeout_ms // 5)):
        app.processEvents()
        if predicate():
            return True
        QTest.qWait(5)
    return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify packaged Lilies pose click masks offscreen"
    )
    parser.add_argument(
        "--executable",
        type=Path,
        default=Path(sys.executable),
        help="executable whose SHA256 identifies this evidence",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="JSON evidence output path",
    )
    parser.add_argument(
        "--resource-root",
        type=Path,
        default=PROJECT_ROOT,
        help="root containing the qml and themes packaged resources",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    executable = args.executable.resolve()
    report_path = args.report_path.resolve()
    resource_root = args.resource_root.resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"pose evidence executable is missing: {executable}")
    if not resource_root.is_dir():
        raise FileNotFoundError(
            f"pose evidence resource root is missing: {resource_root}"
        )

    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([])
    backend = MaskBackend(resource_root)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.loadData(
        HARNESS,
        QUrl.fromLocalFile(str(resource_root / "qml" / "PoseMaskHarness.qml")),
    )
    if not engine.rootObjects():
        raise RuntimeError("pose click-mask harness failed to load")
    window = engine.rootObjects()[0]
    pet = window.findChild(QQuickItem, "maskPet")
    if pet is None:
        raise RuntimeError("pose click-mask pet did not load")
    frame = pet.findChild(QQuickItem, "petPoseArtworkFrame")
    if frame is None:
        raise RuntimeError("pose click-mask frame did not load")

    character = dict(backend.themeManifest["character"])
    specifications = dict(character["poseArtworkSpecs"])
    cases = (
        ("perch-prone", "posePerchProne"),
        ("title-sit", "poseTitleSit"),
        ("edge-peek-live", "poseEdgePeek"),
        ("listening-live", "poseListeningLive"),
        ("focus-watch", "poseFocusKneel"),
    )
    results: dict[str, object] = {}
    backend.set_habitat({})
    for pose_id, asset_key in cases:
        backend.set_pose(pose_id)
        QTest.qWait(330)
        mask = dict(specifications[asset_key]["clickMask"])
        hit_x, hit_y = _first_mask_point(mask)
        declared_hit = _hit(pet, frame, hit_x, hit_y)
        transparent_corners = [
            _hit(pet, frame, 0.001, 0.001),
            _hit(pet, frame, 0.999, 0.001),
            _hit(pet, frame, 0.001, 0.999),
            _hit(pet, frame, 0.999, 0.999),
        ]
        result = {
            "assetKey": str(pet.property("poseArtworkKey")),
            "maskType": str(mask.get("type", "")),
            "declaredHitPoint": [hit_x, hit_y],
            "declaredHit": declared_hit,
            "transparentCornerHits": transparent_corners,
            "passed": (
                str(pet.property("poseArtworkKey")) == asset_key
                and declared_hit
                and not any(transparent_corners)
            ),
        }
        results[pose_id] = result

    # A user should not need pixel-perfect aim at an anti-aliased hair or
    # dress edge.  The perch fixture uses a declared ellipse with a known
    # right-hand boundary; probe three logical pixels beyond it.  The exact
    # asset mask must remain false there while the shared native/QML
    # interaction mask accepts it.  Distant transparent corners must continue
    # to pass through.
    backend.set_pose("perch-prone")
    QTest.qWait(330)
    perch_mask = dict(specifications["posePerchProne"]["clickMask"])
    center_x, center_y, radius_x, _radius_y = (
        float(value) for value in perch_mask["ellipse"]
    )
    near_edge_x = center_x + radius_x + 3.0 / max(1.0, float(frame.width()))
    exact_near_edge = _hit(pet, frame, near_edge_x, center_y)
    interaction_near_edge = _interaction_hit(
        pet, frame, near_edge_x, center_y
    )
    interaction_corner_hits = [
        _interaction_hit(pet, frame, 0.001, 0.001),
        _interaction_hit(pet, frame, 0.999, 0.001),
        _interaction_hit(pet, frame, 0.001, 0.999),
        _interaction_hit(pet, frame, 0.999, 0.999),
    ]
    interaction_tolerance_result = {
        "probeDistanceLogicalPx": 3.0,
        "exactNearEdgeHit": exact_near_edge,
        "interactionNearEdgeHit": interaction_near_edge,
        "transparentCornerHits": interaction_corner_hits,
        "passed": (
            not exact_near_edge
            and interaction_near_edge
            and not any(interaction_corner_hits)
        ),
    }

    backend.set_pose("edge-peek-live")
    backend.set_habitat({})
    QTest.qWait(330)
    unmirrored_left = _hit(pet, frame, 0.05, 0.50)
    unmirrored_right = _hit(pet, frame, 0.95, 0.50)
    backend.set_habitat(
        {
            "attached": True,
            "profile": "mask-mirror",
            "poseVariant": "mask-mirror-fixture",
            "pose": "edge-peek-right",
            "habitatStrategy": "edge",
            "characterScale": 0.82,
            "anchorNormX": 0.50,
            "anchorNormY": 0.48,
            "contactX": 0.18,
            "contactY": 0.58,
            "mirror": True,
            "motionStyle": "quiet-breathe",
            "motionPeriod": 4.0,
            "peekFraction": 0.72,
        }
    )
    QTest.qWait(330)
    mirrored_left = _hit(pet, frame, 0.05, 0.50)
    mirrored_right = _hit(pet, frame, 0.95, 0.50)
    mirror_result = {
        "displayedMirror": bool(frame.property("displayedMirror")),
        "unmirroredLeft": unmirrored_left,
        "unmirroredRight": unmirrored_right,
        "mirroredLeft": mirrored_left,
        "mirroredRight": mirrored_right,
        "passed": (
            not unmirrored_left
            and unmirrored_right
            and mirrored_left
            and not mirrored_right
        ),
    }


    # Build two settled 19x19 masks, then prove the transition uses only the
    # actually visible slot at each endpoint and their union in the middle.
    backend.set_habitat({})
    backend.set_pose("reading")
    QTest.qWait(420)
    reading_grid = _hit_grid(pet, frame)
    backend.set_pose("perch-prone")
    QTest.qWait(420)
    perch_grid = _hit_grid(pet, frame)
    retained_source = str(frame.property("displayedSource"))
    backend.set_pose("reading")
    app.processEvents()
    loading_retained_old = str(frame.property("displayedSource")) == retained_source
    cached_ready_transition = bool(frame.property("transitionActive"))
    transition_started = _wait_for(
        app, lambda: bool(frame.property("transitionActive")), timeout_ms=1500
    )
    if transition_started:
        frame.setProperty("presentationProgress", 0.0)
    progress_zero_grid = _hit_grid(pet, frame)
    if transition_started:
        frame.setProperty("presentationProgress", 0.5)
    midpoint_grid = _hit_grid(pet, frame)
    if transition_started:
        frame.setProperty("presentationProgress", 1.0)
    progress_one_grid = _hit_grid(pet, frame)
    expected_union = {
        point: perch_grid[point] or reading_grid[point] for point in perch_grid
    }
    old_only = [
        list(point) for point in perch_grid
        if perch_grid[point] and not reading_grid[point]
    ]
    new_only = [
        list(point) for point in reading_grid
        if reading_grid[point] and not perch_grid[point]
    ]
    transition_result = {
        "gridSize": [19, 19],
        "loadingRetainedOldSource": loading_retained_old,
        "cachedTargetEnteredReadyTransition": cached_ready_transition,
        "unreadyTargetNeverCommitted": (
            loading_retained_old or cached_ready_transition
        ),
        "transitionStartedAfterReady": transition_started,
        "progressZeroUsesOldOnly": progress_zero_grid == perch_grid,
        "midpointUsesVisibleUnion": midpoint_grid == expected_union,
        "progressOneUsesNewOnly": progress_one_grid == reading_grid,
        "oldOnlyCellCount": len(old_only),
        "newOnlyCellCount": len(new_only),
        "oldOnlyExamples": old_only[:8],
        "newOnlyExamples": new_only[:8],
    }
    transition_result["passed"] = bool(
        all(
            transition_result[key]
            for key in (
                "unreadyTargetNeverCommitted",
                "transitionStartedAfterReady",
                "progressZeroUsesOldOnly",
                "midpointUsesVisibleUnion",
                "progressOneUsesNewOnly",
            )
        )
        and old_only and new_only
    )

    # A failed target must retire artwork presentation and converge on the
    # truthful layered renderer for every outfit.  The previous Ready bitmap
    # may stay cached in its slot, but must neither flash nor own hit regions.
    QTest.qWait(320)
    backend.set_pose("perch-prone")
    QTest.qWait(420)
    backend.override_asset(
        "poseExpansionSheet",
        QUrl.fromLocalFile(str(resource_root / "missing-pose-artwork.png")),
    )
    backend.set_pose("reading")
    failed = _wait_for(
        app, lambda: bool(frame.property("targetLoadFailed")), timeout_ms=1500
    )
    QTest.qWait(260)
    failure_outfits: dict[str, object] = {}
    for outfit_id in (
        "first-encounter",
        "summer-cotton-dress",
        "home-cardigan",
        "reading-smock",
        "focus-coat",
        "rest-nightdress",
    ):
        backend.set_outfit(outfit_id)
        QTest.qWait(260)
        blend = float(pet.property("renderedArtworkBlend"))
        layered_hit = _hit(pet, frame, 0.50, 0.50)
        support_point = pet.property("supportCordPoint")
        layered_support_point = pet.property("layeredSupportCordPoint")
        cord_fallback_drift = (
            (float(support_point.x()) - float(layered_support_point.x())) ** 2
            + (float(support_point.y()) - float(layered_support_point.y())) ** 2
        ) ** 0.5
        failure_outfits[outfit_id] = {
            "artworkBlend": round(blend, 4),
            "layeredFrameVisible": bool(
                pet.findChild(QQuickItem, "petFigureFrame").isVisible()
            ),
            "layeredHit": layered_hit,
            "cordFallbackDrift": round(cord_fallback_drift, 4),
            "passed": (
                blend <= 0.001
                and layered_hit
                and cord_fallback_drift <= 0.01
            ),
        }
    failure_result = {
        "targetLoadFailed": failed,
        "outfits": failure_outfits,
        "passed": failed and all(
            bool(value["passed"]) for value in failure_outfits.values()
        ),
    }

    # Supersede in-flight requests repeatedly.  The last pose alone must be
    # committed, with no orphan loading slot or half-finished cross-fade.
    backend.override_asset("poseExpansionSheet", None)
    backend.set_outfit("first-encounter")
    backend.set_pose("perch-prone")
    QTest.qWait(420)
    for pose_id in (
        "reading", "presenting", "box-support", "resting",
        "focus-watch", "listening-live", "edge-peek-live", "title-sit",
    ):
        backend.set_pose(pose_id)
        QTest.qWait(5)
    rapid_settled = _wait_for(
        app,
        lambda: (
            str(pet.property("poseArtworkKey")) == "poseTitleSit"
            and "lilith-pose-title-sit-v1.png"
            in str(frame.property("displayedSource"))
            and int(frame.property("loadingSlot")) == 0
            and not bool(frame.property("transitionActive"))
            and abs(float(pet.property("renderedArtworkBlend")) - 1.0) <= 0.001
        ),
        timeout_ms=2500,
    )
    rapid_result = {
        "finalAssetKey": str(pet.property("poseArtworkKey")),
        "displayedSource": str(frame.property("displayedSource")),
        "loadingSlot": int(frame.property("loadingSlot")),
        "transitionActive": bool(frame.property("transitionActive")),
        "artworkBlend": round(float(pet.property("renderedArtworkBlend")), 4),
        "passed": rapid_settled,
    }
    application_version = str(backend.themeManifest.get("version", "")).strip()
    report = {
        "schemaVersion": 1,
        "applicationVersion": application_version,
        "executableSha256": hashlib.sha256(
            executable.read_bytes()
        ).hexdigest().upper(),
        "resourceRoot": str(resource_root),
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "poses": results,
        "interactionTolerance": interaction_tolerance_result,
        "mirror": mirror_result,
        "transition": transition_result,
        "loadFailure": failure_result,
        "rapidSwitch": rapid_result,
        "passed": (
            bool(application_version)
            and all(bool(dict(value)["passed"]) for value in results.values())
            and bool(interaction_tolerance_result["passed"])
            and bool(mirror_result["passed"])
            and bool(transition_result["passed"])
            and bool(failure_result["passed"])
            and bool(rapid_result["passed"])
        ),
        "capturedAt": datetime.now(UTC).isoformat(),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    window.hide()
    window.close()
    app.quit()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
